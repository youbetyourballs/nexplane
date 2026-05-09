"""Executor for agent_containerize_retire change type."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch containerize_retire to the registered Nexplane agent."""
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    systemd_unit = parameters.get("systemd_unit", "")
    if not systemd_unit:
        raise ValueError("Missing required parameter: systemd_unit")

    return await dispatch_agent_job(
        command="containerize_retire",
        parameters={
            "systemd_unit": systemd_unit,
            "dry_run": bool(parameters.get("dry_run", False)),
        },
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: restart the legacy service."""
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    systemd_unit = parameters.get("systemd_unit") or execution_result.get("systemd_unit", "")
    if not systemd_unit:
        return {"rolled_back": False, "note": "No systemd_unit for rollback"}

    result = await dispatch_agent_job(
        command="containerize_retire",
        parameters={"systemd_unit": systemd_unit, "dry_run": False},
        asset_ids=list(asset_ids) if (asset_ids := parameters.get("asset_ids", [])) else [],
        timeout_seconds=60,
        rollback=True,
    )
    return result
