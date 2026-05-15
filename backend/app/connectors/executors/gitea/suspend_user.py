from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_gitea_client, GiteaClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_gitea_client(connector)
    if not client and parameters.get("gitea_url"):
        client = GiteaClient(url=parameters["gitea_url"], token=parameters.get("gitea_token", ""))
    if not client:
        return {"action": "gitea_suspend_user", "status": "skipped",
                "reason": "no_gitea_credentials", "username": username}
    result = await client.suspend_user(username)
    return {**result, "action": "gitea_suspend_user",
            "suspended_at": datetime.now(timezone.utc).isoformat(),
            "_asset_ids": [str(a) for a in asset_ids]}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_gitea_client(connector)
    if not client and parameters.get("gitea_url"):
        client = GiteaClient(url=parameters["gitea_url"], token=parameters.get("gitea_token", ""))
    if not client:
        return {"rolled_back": False, "reason": "no_gitea_credentials"}
    result = await client.unsuspend_user(username)
    return {"rolled_back": result.get("success", False), **result}
