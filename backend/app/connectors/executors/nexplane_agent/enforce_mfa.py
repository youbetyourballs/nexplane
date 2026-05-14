async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.aws.enforce_mfa_iam import execute as iam_mfa
    result = await iam_mfa(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.enforce_mfa_iam import rollback as iam_rollback
    return await iam_rollback(parameters, execution_result, connector)
