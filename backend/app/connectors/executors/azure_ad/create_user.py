import secrets
import string
from datetime import datetime, timezone
from .azure_ad_client import get_azure_ad_client


def _random_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(chars) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    upn = parameters.get("user_principal_name", "")
    display_name = parameters.get("display_name") or upn.split("@")[0]
    password = parameters.get("initial_password") or _random_password()
    if not upn:
        raise ValueError("user_principal_name is required")
    client = get_azure_ad_client(connector)
    if not client:
        return {
            "action": "azure_ad_create_user",
            "upn": upn,
            "status": "skipped",
            "reason": "no_azure_ad_credentials",
        }
    result = await client.create_user(display_name, upn, password)
    return {
        **result,
        "action": "azure_ad_create_user",
        "initial_password": password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    upn = parameters.get("user_principal_name", "")
    user_id = execution_result.get("id") or upn
    client = get_azure_ad_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_azure_ad_credentials"}
    result = await client.delete_user(user_id)
    return {"rolled_back": True, **result}
