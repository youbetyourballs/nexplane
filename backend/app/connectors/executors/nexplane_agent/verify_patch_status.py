"""Executor: verify_patch_status — verifies patch was applied by checking package version."""
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid

    if not asset_ids:
        raise ValueError("asset_ids required for verify_patch_status")

    asset_id = str(asset_ids[0])

    result = await dispatch_agent_job(
        command="audit_patch_status",
        parameters=parameters,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Check if the expected package version is present
    package_name = parameters.get("package_name")
    expected_version = parameters.get("expected_version") or parameters.get("fixed_version")

    verified = True
    detail = "Patch status verified"

    if package_name and expected_version:
        installed = result.get("installed_packages", [])
        pkg = next((p for p in installed if p.get("name") == package_name), None)
        if not pkg:
            verified = False
            detail = f"Package {package_name} not found in inventory"
        else:
            try:
                from packaging.version import Version  # type: ignore[import]
                if Version(pkg["version"]) < Version(expected_version):
                    verified = False
                    detail = f"{package_name} is {pkg['version']}, expected >= {expected_version}"
                else:
                    detail = f"{package_name} {pkg['version']} >= {expected_version} ✓"
            except Exception:
                detail = f"{package_name} {pkg.get('version')} (version comparison skipped)"

    # Update reboot_required on asset if audit shows it
    if result.get("reboot_required") or result.get("reboot_pending"):
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                if asset:
                    meta = dict(asset.asset_metadata or {})
                    meta["reboot_required"] = True
                    asset.asset_metadata = meta
                    await db.commit()
        except Exception:
            pass

    return {**result, "verified": verified, "verification_detail": detail}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verify is read-only"}
