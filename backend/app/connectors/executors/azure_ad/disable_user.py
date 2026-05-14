from datetime import datetime, timezone
from .azure_ad_client import get_azure_ad_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier") or parameters.get("user_name", "")
    if not user:
        raise ValueError("user_identifier is required")
    client = get_azure_ad_client(connector)
    if not client:
        return {
            "action": "azure_ad_disable_user",
            "user": user,
            "status": "skipped",
            "reason": "no_azure_ad_credentials",
            "_asset_ids": [str(a) for a in asset_ids],
        }
    result = await client.disable_user(user)
    await client.revoke_sessions(user)
    return {
        **result,
        "sessions_revoked": True,
        "disabled_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = parameters.get("user_identifier", "")
    client = get_azure_ad_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_azure_ad_credentials"}
    result = await client.enable_user(user)
    return {"rolled_back": True, **result}
