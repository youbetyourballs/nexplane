"""
Discovers all assets that violate a patch age baseline and optionally creates
patch_packages change requests to remediate each non-compliant host.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Params:
        max_patch_age_days    int   default 30   Flag hosts whose last security update
                                                  is older than this many days.
        asset_filter          dict  optional     e.g. {"tags": {"env": "prod"}}
        os_family             str   optional     "linux" | "windows" | None (both)
        create_change_request bool  default False Auto-create a patch_packages CR for
                                                  each non-compliant host.

    Returns:
        compliant_hosts       list[str]   asset IDs that pass
        non_compliant_hosts   list[dict]  [{asset_id, hostname, os_family,
                                            days_since_patch, change_request_id?}]
        total_assets_checked  int
    """
    max_age = int(parameters.get("max_patch_age_days", 30))
    asset_filter = parameters.get("asset_filter", {})
    os_family_filter = parameters.get("os_family")
    auto_cr = bool(parameters.get("create_change_request", False))

    assets = await connector.asset_repository.list_assets(filter=asset_filter)

    compliant: list[str] = []
    non_compliant: list[dict] = []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age)

    for asset in assets:
        meta = asset.metadata or {}
        af = meta.get("os_family")

        if os_family_filter and af != os_family_filter:
            continue

        last_patched_raw = meta.get("last_security_patch_at")
        if not last_patched_raw:
            days_since: int | None = None
        else:
            last_patched = datetime.fromisoformat(last_patched_raw)
            if last_patched.tzinfo is None:
                last_patched = last_patched.replace(tzinfo=timezone.utc)
            days_since = (datetime.now(tz=timezone.utc) - last_patched).days

        if days_since is not None and days_since <= max_age:
            compliant.append(asset.id)
            continue

        entry: dict[str, Any] = {
            "asset_id": asset.id,
            "hostname": asset.hostname,
            "os_family": af,
            "days_since_patch": days_since,
        }

        if auto_cr:
            cr = await connector.change_request_service.create(
                change_type="patch_packages",
                asset_id=asset.id,
                params={
                    "os_family": af,
                    "mode": "security_only",
                    "dry_run": False,
                },
                title=f"Security patch compliance: {asset.hostname}",
                description=(
                    f"Host is {days_since if days_since is not None else 'unknown'} days "
                    f"behind on security patches (threshold: {max_age} days)."
                ),
            )
            entry["change_request_id"] = cr.id

        non_compliant.append(entry)

    return {
        "compliant_hosts": compliant,
        "non_compliant_hosts": non_compliant,
        "total_assets_checked": len(compliant) + len(non_compliant),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Compliance check is read-only (or creates CRs); nothing to roll back.
    return {"rolled_back": False, "reason": "check_patch_compliance is read-only"}
