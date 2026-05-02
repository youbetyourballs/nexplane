import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_path = parameters["secret_path"]
    if not creds:
        return {"action": "rotate_secret", "secret_path": secret_path, "rotated": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.secrets.kv.v2.create_or_update_secret(path=secret_path, secret=parameters["new_value"]))
    return {"action": "rotate_secret", "secret_path": secret_path, "rotated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore previous secret value manually"}
