import asyncio
from datetime import datetime, timezone


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_iam_client
    iam = get_iam_client(creds)
    loop = asyncio.get_event_loop()
    username = parameters['username']
    old_key_id = parameters.get('old_access_key_id')

    def _call():
        new_key = iam.create_access_key(UserName=username)['AccessKey']
        if old_key_id:
            iam.update_access_key(UserName=username, AccessKeyId=old_key_id, Status='Inactive')
        return {"new_access_key_id": new_key['AccessKeyId'], "new_secret_access_key": new_key['SecretAccessKey']}

    result = await loop.run_in_executor(None, _call)
    return {"action": "rotate_iam_key", "username": username, "executed_at": datetime.now(timezone.utc).isoformat(), **result}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "rotate_iam_key", "username": parameters.get('username'), "new_access_key_id": "AKIAMOCK000000000001", "new_secret_access_key": "mockSecretKey", "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key rotation cannot be automatically rolled back — deactivate new key manually"}
