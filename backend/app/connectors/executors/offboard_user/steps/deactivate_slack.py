from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Deactivate the Slack user account."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "deactivate_slack_member",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        lookup_resp = await client.get(
            "/users.lookupByEmail",
            headers=headers,
            params={"email": target_email},
        )
        lookup_resp.raise_for_status()
        data = lookup_resp.json()
        if not data.get("ok"):
            return {
                "action": "deactivate_slack_member",
                "target_email": target_email,
                "skipped": True,
                "reason": data.get("error", "user not found"),
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        user_id = data["user"]["id"]

        deactivate_resp = await client.post(
            "/admin.users.remove",
            headers=headers,
            json={"user_id": user_id},
        )
        deactivate_resp.raise_for_status()

    return {
        "action": "deactivate_slack_member",
        "target_email": target_email,
        "slack_user_id": user_id,
        "deactivated": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "reinstate_slack_member",
        "target_email": parameters["target_email"],
        "slack_user_id": execution_result.get("slack_user_id"),
        "rolled_back": True,
    }
