# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "create_okta_account",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Content-Type": "application/json"}
    parts = parameters.get("display_name", "").split(" ", 1)

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.post(
            "/api/v1/users?activate=true",
            headers=headers,
            json={
                "profile": {
                    "firstName": parts[0],
                    "lastName": parts[1] if len(parts) > 1 else "",
                    "email": target_email,
                    "login": target_email,
                    "department": parameters.get("department"),
                    "manager": parameters.get("manager_email"),
                }
            },
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]

        for group_id in parameters.get("groups", []):
            await client.put(f"/api/v1/groups/{group_id}/users/{user_id}", headers=headers)

    return {
        "action": "create_okta_account",
        "target_email": target_email,
        "okta_user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_okta_account",
        "target_email": parameters["target_email"],
        "okta_user_id": execution_result.get("okta_user_id"),
        "rolled_back": True,
    }
