"""Executor: apply_patches — dispatches apply_linux_patches or apply_windows_patches to the agent."""
from __future__ import annotations
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid

    if not asset_ids:
        raise ValueError("asset_ids required for apply_patches")

    asset_id = asset_ids[0]

    # Determine OS from asset metadata
    os_family = parameters.get("os_family", "linux")
    if not parameters.get("os_family"):
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(str(asset_id)))
                if asset:
                    meta = asset.asset_metadata or {}
                    os_family = meta.get("os_family", "linux")
        except Exception:
            pass

    command = "apply_windows_patches" if os_family == "windows" else "apply_linux_patches"

    result = await dispatch_agent_job(
        command=command,
        parameters={
            "mode": parameters.get("mode", "security_only"),
            "package_name": parameters.get("package_name", ""),
            "cve_id": parameters.get("cve_id", ""),
            "kb_id": parameters.get("kb_id", ""),
            "dry_run": bool(parameters.get("dry_run", False)),
            "defer_reboot": bool(parameters.get("defer_reboot", False)),
        },
        asset_ids=[str(asset_id)],
        timeout_seconds=600,
    )

    # Write last_security_patch_at to asset metadata
    if not parameters.get("dry_run"):
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(str(asset_id)))
                if asset:
                    meta = dict(asset.asset_metadata or {})
                    meta["last_security_patch_at"] = datetime.now(timezone.utc).isoformat()
                    if result.get("packages_updated"):
                        meta["last_patch_packages"] = result["packages_updated"]
                    if result.get("reboot_required"):
                        meta["reboot_required"] = True
                    asset.asset_metadata = meta
                    await db.commit()
        except Exception:
            pass

    return result
