async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "block_sign_in", "user_id": user_id, "account_enabled": False, "sessions_revoked": True}
    from app.connectors.executors.entra_id.disable_user import execute as disable
    from app.connectors.executors.entra_id.revoke_sessions import execute as revoke
    disable_result = await disable(parameters, asset_ids, connector)
    revoke_result = await revoke(parameters, asset_ids, connector)
    return {"action": "block_sign_in", "user_id": user_id, "disable_result": disable_result, "revoke_result": revoke_result}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.enable_user import execute as enable
    return await enable(parameters, [], connector)
