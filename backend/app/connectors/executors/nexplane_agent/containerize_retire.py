"""Executor for agent_containerize_retire change type."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch containerize_retire to the registered Nexplane agent."""
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    systemd_unit = parameters.get("systemd_unit", "")
    if not systemd_unit:
        raise ValueError("Missing required parameter: systemd_unit")

    result = await dispatch_agent_job(
        command="containerize_retire",
        parameters={
            "systemd_unit": systemd_unit,
            "dry_run": bool(parameters.get("dry_run", False)),
        },
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    # Embed asset_ids in result so rollback can target the same hosts
    if isinstance(result, dict):
        result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: restart the legacy service."""
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    systemd_unit = parameters.get("systemd_unit") or execution_result.get("systemd_unit", "")
    if not systemd_unit:
        return {"rolled_back": False, "note": "No systemd_unit for rollback"}

    # asset_ids are embedded in execution_result by execute() above
    rollback_asset_ids = execution_result.get("_asset_ids", [])
    if not rollback_asset_ids:
        return {"rolled_back": False, "note": "No asset_ids available for rollback dispatch"}

    return await dispatch_agent_job(
        command="containerize_retire",
        parameters={"systemd_unit": systemd_unit, "dry_run": False},
        asset_ids=rollback_asset_ids,
        timeout_seconds=60,
        rollback=True,
    )
