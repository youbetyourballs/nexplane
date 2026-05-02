import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "create_policy", "policy_name": policy_name, "created": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.create_or_update_policy(name=policy_name, policy=parameters["policy_hcl"]))
    return {"action": "create_policy", "policy_name": policy_name, "created": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.hashicorp_vault.delete_policy import execute as delete
    return await delete(parameters, [], connector)
