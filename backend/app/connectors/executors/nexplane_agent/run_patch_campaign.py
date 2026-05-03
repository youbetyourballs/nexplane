"""
Identifies all assets affected by a CVE or package vulnerability and applies
patches in rolling batches, aborting if the error rate exceeds the threshold.
"""
from __future__ import annotations
import asyncio
from typing import Any


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Params: see patch_campaign.json parameter schema.

    Returns:
        affected_assets    int
        batches_completed  int
        hosts_patched      list[str]   asset IDs successfully patched
        hosts_failed       list[dict]  [{asset_id, hostname, error}]
        aborted            bool
        abort_reason       str | None
    """
    cve_id = parameters.get("cve_id")
    package_name = parameters.get("package_name")
    affected_version_lt = parameters.get("affected_version_lt")
    asset_filter = parameters.get("asset_filter", {})
    batch_size = int(parameters.get("batch_size", 10))
    abort_threshold = float(parameters.get("abort_error_threshold", 0.2))
    dry_run = bool(parameters.get("dry_run", False))
    defer_reboot = bool(parameters.get("defer_reboot", False))

    if not cve_id and not package_name:
        raise ValueError("At least one of cve_id or package_name is required")

    affected = await _find_affected_assets(
        connector, asset_filter, package_name, affected_version_lt, cve_id
    )

    if not affected:
        return {
            "affected_assets": 0,
            "batches_completed": 0,
            "hosts_patched": [],
            "hosts_failed": [],
            "aborted": False,
            "abort_reason": None,
        }

    patched: list[str] = []
    failed: list[dict] = []
    aborted = False
    abort_reason: str | None = None
    batch_num = 0

    batches = [affected[i:i + batch_size] for i in range(0, len(affected), batch_size)]

    for batch_num, batch in enumerate(batches):
        results = await asyncio.gather(
            *[
                _patch_single_host(connector, asset, parameters, dry_run, defer_reboot)
                for asset in batch
            ],
            return_exceptions=True,
        )

        for asset, result in zip(batch, results):
            if isinstance(result, Exception):
                failed.append({
                    "asset_id": asset["id"],
                    "hostname": asset["hostname"],
                    "error": str(result),
                })
            else:
                patched.append(asset["id"])

        total_attempted = len(patched) + len(failed)
        if total_attempted > 0 and len(failed) / total_attempted > abort_threshold:
            aborted = True
            abort_reason = (
                f"Error rate {len(failed)/total_attempted:.0%} exceeded threshold "
                f"{abort_threshold:.0%} after batch {batch_num + 1}"
            )
            break

    return {
        "affected_assets": len(affected),
        "batches_completed": batch_num + 1 if not aborted else batch_num,
        "hosts_patched": patched,
        "hosts_failed": failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Campaign rollback is not supported at the campaign level — individual
    # patch_packages change requests each have their own rollback.
    return {
        "rolled_back": False,
        "reason": "patch_campaign rollback must be executed per-host via individual patch_packages change requests",
    }


async def _find_affected_assets(
    connector: Any,
    asset_filter: dict,
    package_name: str | None,
    affected_version_lt: str | None,
    cve_id: str | None,
) -> list[dict]:
    """
    Query asset metadata for installed software inventory.
    """
    assets = await connector.asset_repository.list_assets(filter=asset_filter)
    affected = []

    for asset in assets:
        meta = asset.metadata or {}
        inventory = meta.get("software_inventory", [])
        advisories = meta.get("security_advisories", [])

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
                    pass  # unparseable version — include conservatively
            affected.append({
                "id": asset.id,
                "hostname": asset.hostname,
                "os_family": meta.get("os_family", "linux"),
                "package_name": pkg_to_check,
                "installed_version": item.get("version"),
            })
            break  # one match per asset is enough

    return affected


async def _patch_single_host(
    connector: Any,
    asset: dict,
    params: dict,
    dry_run: bool,
    defer_reboot: bool,
) -> None:
    """
    Dispatches an agent command to patch a single host.
    Raises RuntimeError on failure so asyncio.gather() captures it as an exception.
    """
    os_family = asset["os_family"]
    cve_id = params.get("cve_id")
    pkg = params.get("package_name") or asset.get("package_name")

    if os_family == "windows":
        command = "apply_windows_patches"
        cmd_params: dict[str, Any] = {
            "mode": "security_only",
            "dry_run": dry_run,
            "defer_reboot": defer_reboot,
        }
        if cve_id:
            cmd_params["note"] = f"Campaign targeting {cve_id}"
    else:
        command = "apply_linux_patches"
        if cve_id:
            cmd_params = {"mode": "cve", "cve_id": cve_id, "dry_run": dry_run}
        elif pkg:
            cmd_params = {"mode": "package", "package_name": pkg, "dry_run": dry_run}
        else:
            cmd_params = {"mode": "security_only", "dry_run": dry_run}

    result = await connector.agent_client.run_command(
        asset_id=asset["id"],
        command=command,
        params=cmd_params,
        timeout_seconds=600,
    )
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error", "unknown agent error"))
