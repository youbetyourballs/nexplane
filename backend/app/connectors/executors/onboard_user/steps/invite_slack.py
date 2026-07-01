# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "invite_slack_member",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        invite_resp = await client.post(
            "/users.invite",
            headers=headers,
            json={"email": target_email},
        )
        invite_data = invite_resp.json()
        user_id = invite_data.get("user", {}).get("id")

        channel_results = []
        for channel_id in parameters.get("channels", []):
            join_resp = await client.post(
                "/conversations.invite",
                headers=headers,
                json={"channel": channel_id, "users": user_id},
            )
            channel_results.append({"channel": channel_id, "ok": join_resp.json().get("ok")})

    return {
        "action": "invite_slack_member",
        "target_email": target_email,
        "slack_user_id": user_id,
        "channels": channel_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "deactivate_slack_member",
        "target_email": parameters["target_email"],
        "slack_user_id": execution_result.get("slack_user_id"),
        "rolled_back": True,
    }
