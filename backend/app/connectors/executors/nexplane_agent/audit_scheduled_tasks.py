from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="audit_scheduled_tasks",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
