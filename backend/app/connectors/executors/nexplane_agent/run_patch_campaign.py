# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Patch campaign executor: dispatches agent patch jobs to affected assets in rolling batches.
Uses software_inventory metadata populated by agent_listpkgs to find affected assets.
"""
from __future__ import annotations
import asyncio
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    cve_id = parameters.get("cve_id")
    package_name = parameters.get("package_name")
    affected_version_lt = parameters.get("affected_version_lt")
    batch_size = int(parameters.get("batch_size", 10))
    abort_threshold = float(parameters.get("abort_error_threshold", 0.2))
    dry_run = bool(parameters.get("dry_run", False))
    defer_reboot = bool(parameters.get("defer_reboot", False))

    if not cve_id and not package_name:
        raise ValueError("At least one of cve_id or package_name is required")

    # Find affected assets — supports both mock connector and DB paths
    if hasattr(connector, "asset_repository"):
        affected = await _find_affected_assets_connector(
            connector, parameters.get("asset_filter", {}), package_name, affected_version_lt, cve_id
        )
    else:
        affected = await _find_affected_assets_db(
            connector, asset_ids, package_name, affected_version_lt, cve_id
        )

    if not affected:
        return {
            "affected_assets": 0, "batches_completed": 0,
            "hosts_patched": [], "hosts_failed": [], "aborted": False, "abort_reason": None,
        }

    patched, failed = [], []
    aborted, abort_reason = False, None
    batch_num = 0
    batches = [affected[i:i + batch_size] for i in range(0, len(affected), batch_size)]

    for batch_num, batch in enumerate(batches):
        results = await asyncio.gather(
            *[_patch_single_host(connector, a, parameters, dry_run, defer_reboot) for a in batch],
            return_exceptions=True,
        )
        for asset, result in zip(batch, results):
            if isinstance(result, Exception):
                failed.append({"asset_id": asset["id"], "hostname": asset.get("hostname"), "error": str(result)})
            else:
                patched.append(asset["id"])

        total = len(patched) + len(failed)
        if total > 0 and len(failed) / total > abort_threshold:
            aborted = True
            abort_reason = f"Error rate {len(failed)/total:.0%} exceeded threshold {abort_threshold:.0%} after batch {batch_num + 1}"
            break

    return {
        "affected_assets": len(affected),
        "batches_completed": batch_num + 1,
        "hosts_patched": patched,
        "hosts_failed": failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
    }


async def _find_affected_assets_connector(connector, asset_filter: dict, package_name, affected_version_lt, cve_id) -> list:
    """Legacy path: use connector.asset_repository (used in unit tests)."""
    assets = await connector.asset_repository.list_assets(filter=asset_filter)
    affected = []

    for asset in assets:
        meta = asset.metadata or {}
        inventory = meta.get("software_inventory", [])
        advisories = meta.get("security_advisories", [])
        os_family = meta.get("os_family", "linux")

        pkg_to_check = package_name
        if not pkg_to_check and cve_id:
            for adv in advisories:
                if adv.get("cve_id") == cve_id:
                    pkg_to_check = adv.get("package")
                    break

        if not pkg_to_check:
            continue

        for item in inventory:
            if item.get("name") != pkg_to_check:
                continue
            if affected_version_lt:
                try:
                    from packaging.version import Version  # type: ignore[import]
                    if not (Version(item["version"]) < Version(affected_version_lt)):
                        continue
                except Exception:
                    pass
            affected.append({
                "id": str(asset.id),
                "hostname": asset.hostname,
                "os_family": os_family,
                "package_name": pkg_to_check,
                "installed_version": item.get("version"),
            })
            break

    return affected


async def _find_affected_assets_db(connector, asset_ids: list, package_name, affected_version_lt, cve_id) -> list:
    """Production path: query DB for server assets with matching software_inventory entries."""
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset, AssetType
    from sqlalchemy import select

    org_id = getattr(connector, "organization_id", None)
    if not org_id:
        return []

    async with AsyncSessionLocal() as db:
        stmt = select(Asset).where(
            Asset.organization_id == org_id,
            Asset.asset_type == AssetType.server,
        )
        if asset_ids:
            stmt = stmt.where(Asset.id.in_([uuid.UUID(str(a)) for a in asset_ids]))
        result = await db.execute(stmt)
        assets = result.scalars().all()

    affected = []
    for asset in assets:
        meta = asset.asset_metadata or {}
        inventory = meta.get("software_inventory", [])
        os_family = meta.get("os_family", "linux")

        pkg_match = None
        if package_name:
            for item in inventory:
                if item.get("name") == package_name:
                    pkg_match = item
                    break
        elif cve_id:
            advisories = meta.get("security_advisories", [])
            for adv in advisories:
                if adv.get("cve_id") == cve_id:
                    for item in inventory:
                        if item.get("name") == adv.get("package"):
                            pkg_match = item
                            break
                    break
            if not pkg_match and not inventory:
                pkg_match = {}

        if pkg_match is None:
            continue

        if affected_version_lt and pkg_match.get("version"):
            try:
                from packaging.version import Version  # type: ignore[import]
                if not (Version(pkg_match["version"]) < Version(affected_version_lt)):
                    continue
            except Exception:
                pass

        affected.append({
            "id": str(asset.id),
            "hostname": asset.name,
            "os_family": os_family,
            "package_name": package_name or (pkg_match.get("name") if pkg_match else None),
            "installed_version": pkg_match.get("version") if pkg_match else None,
        })

    return affected


async def _patch_single_host(connector, asset: dict, params: dict, dry_run: bool, defer_reboot: bool) -> None:
    """
    Dispatches an agent patch command to a single host.
    Supports both mock connector.agent_client (unit tests) and dispatch_agent_job (production).
    Raises RuntimeError on failure so asyncio.gather() captures it as an exception.
    """
    os_family = asset.get("os_family", "linux")
    cve_id = params.get("cve_id")
    pkg = params.get("package_name") or asset.get("package_name")

    if os_family == "windows":
        command = "apply_windows_patches"
        cmd_params = {"mode": "security_only" if not cve_id else "cve",
                      "dry_run": dry_run, "defer_reboot": defer_reboot}
        if cve_id:
            cmd_params["cve_id"] = cve_id
    else:
        command = "apply_linux_patches"
        if cve_id:
            cmd_params = {"mode": "cve", "cve_id": cve_id, "dry_run": dry_run, "defer_reboot": defer_reboot}
        elif pkg:
            cmd_params = {"mode": "package", "package_name": pkg, "dry_run": dry_run, "defer_reboot": defer_reboot}
        else:
            cmd_params = {"mode": "security_only", "dry_run": dry_run, "defer_reboot": defer_reboot}

    # Mock connector path (unit tests)
    if hasattr(connector, "agent_client"):
        result = await connector.agent_client.run_command(
            asset_id=asset["id"],
            command=command,
            params=cmd_params,
            timeout_seconds=600,
        )
        if result.get("status") != "completed":
            raise RuntimeError(result.get("error", "unknown agent error"))
        return

    # Production path
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    result = await dispatch_agent_job(
        command=command,
        parameters=cmd_params,
        asset_ids=[asset["id"]],
        timeout_seconds=600,
    )
    if result.get("status") == "failed":
        raise RuntimeError(result.get("error", "agent patch job failed"))

    await _update_patch_timestamp(asset["id"])


async def _update_patch_timestamp(asset_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid as _uuid
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, _uuid.UUID(asset_id))
            if asset:
                meta = dict(asset.asset_metadata or {})
                meta["last_security_patch_at"] = datetime.now(timezone.utc).isoformat()
                asset.asset_metadata = meta
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to update patch timestamp for {asset_id}: {e}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Campaign rollback must be executed per-host via individual patch_packages change requests. Use the hosts_failed list to identify which assets need manual intervention.",
    }
