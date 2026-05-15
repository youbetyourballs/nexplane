async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.vault.rotate_secret import execute as vault_rotate
    result = await vault_rotate(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.vault.rotate_secret import rollback as vault_rb
    return await vault_rb(parameters, execution_result, connector)
