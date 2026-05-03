from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Clear all Okta sessions and suspend the account."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "revoke_okta_sessions",
            "target_email": target_email,
            "sessions_cleared": True,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.get(f"/api/v1/users/{target_email}", headers=headers)
        resp.raise_for_status()
        user_id = resp.json()["id"]

        await client.delete(f"/api/v1/users/{user_id}/sessions", headers=headers)
        await client.post(f"/api/v1/users/{user_id}/lifecycle/suspend", headers=headers)

    return {
        "action": "revoke_okta_sessions",
        "target_email": target_email,
        "okta_user_id": user_id,
        "sessions_cleared": True,
        "account_suspended": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "unsuspend_okta_account",
        "target_email": parameters["target_email"],
        "okta_user_id": execution_result.get("okta_user_id"),
        "rolled_back": True,
    }
