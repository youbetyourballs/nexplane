from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="seccomp_learn",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("duration_seconds", 60)) + 30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "seccomp_learn creates no persistent state"}
