from .azure_ad_client import get_azure_ad_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user_id = parameters.get("user_id", "")
    if not user_id:
        raise ValueError("user_id is required")
    client = get_azure_ad_client(connector)
    if not client:
        return {"action": "azure_ad_get_group_membership", "status": "skipped", "reason": "no_azure_ad_credentials"}
    groups = await client.get_group_membership(user_id)
    return {
        "action": "azure_ad_get_group_membership",
        "user_id": user_id,
        "groups": groups,
        "count": len(groups),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
