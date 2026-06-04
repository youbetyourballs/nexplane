from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="defaults_write",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    previous_value = execution_result.get("previous_value")
    if previous_value is not None:
        # Restore the key to its previous value
        rollback_params = {
            "domain": parameters.get("domain"),
            "key": parameters.get("key"),
            "value": previous_value,
            "type": parameters.get("type", "string"),
        }
        return await _dispatch.dispatch_agent_job(
            command="defaults_write",
            parameters=rollback_params,
            asset_ids=asset_ids,
            timeout_seconds=30,
        )
    else:
        # Key did not exist before — delete it
        rollback_params = {
            "domain": parameters.get("domain"),
            "key": parameters.get("key"),
        }
        return await _dispatch.dispatch_agent_job(
            command="defaults_delete",
            parameters=rollback_params,
            asset_ids=asset_ids,
            timeout_seconds=30,
        )
