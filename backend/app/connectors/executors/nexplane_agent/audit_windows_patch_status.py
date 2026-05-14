from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="audit_windows_patch_status",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result
