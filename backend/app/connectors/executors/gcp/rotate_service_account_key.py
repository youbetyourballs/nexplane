import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sa_email = parameters["service_account_email"]
    old_key_id = parameters["old_key_id"]
    if not creds:
        return {"action": "rotate_service_account_key", "service_account_email": sa_email, "new_key_id": "mock-new-key-id", "old_key_deleted": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import iam_admin_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = iam_admin_v1.IAMClient(credentials=credentials)
    sa_name = f"projects/{project}/serviceAccounts/{sa_email}"

    new_key = await loop.run_in_executor(None, lambda: client.create_service_account_key(name=sa_name))
    new_key_id = new_key.name.split("/")[-1]

    old_key_name = f"{sa_name}/keys/{old_key_id}"
    await loop.run_in_executor(None, lambda: client.delete_service_account_key(name=old_key_name))

    return {
        "action": "rotate_service_account_key",
        "service_account_email": sa_email,
        "new_key_id": new_key_id,
        "private_key_data": new_key.private_key_data.decode("utf-8") if new_key.private_key_data else None,
        "old_key_deleted": True,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key rotation cannot be undone automatically"}
