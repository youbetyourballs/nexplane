from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="sudoers_audit",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "read-only scan"}
