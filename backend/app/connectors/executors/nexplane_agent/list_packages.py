"""Executor: list_installed_packages — CIS Control 2 software inventory via Nexplane agent."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch list_installed_packages to the Nexplane agent on the target host.

    On success the result is written to asset_metadata.software_inventory so
    downstream queries can access the package list without re-running the job.
    """
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    result = await dispatch_agent_job(
        command="list_installed_packages",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )

    # Persist to asset metadata when possible (best-effort, non-fatal)
    try:
        await _write_software_inventory(asset_ids, result)
    except Exception:
        pass

    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_installed_packages is read-only"}


async def _write_software_inventory(asset_ids: list, result: dict) -> None:
    """Write package list to asset_metadata.software_inventory for the first asset."""
    import uuid
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    if not asset_ids:
        return

    asset_id = uuid.UUID(asset_ids[0]) if isinstance(asset_ids[0], str) else asset_ids[0]

    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if asset is None:
            return
        metadata = dict(asset.metadata or {})
        metadata["software_inventory"] = {
            "packages": result.get("packages", []),
            "manager": result.get("manager"),
            "total": result.get("total", 0),
            "listed_at": result.get("listed_at"),
        }
        asset.metadata = metadata
        await db.commit()
