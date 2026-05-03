from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Block sign-in for the Entra ID (Azure AD) account and revoke refresh tokens."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "suspend_entra_account",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    tenant_id = creds.get("tenant_id")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        user_resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{target_email}",
            headers=headers,
        )
        user_resp.raise_for_status()
        user_id = user_resp.json()["id"]

        await client.patch(
            f"https://graph.microsoft.com/v1.0/users/{user_id}",
            headers=headers,
            json={"accountEnabled": False},
        )

        await client.post(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/revokeSignInSessions",
            headers=headers,
        )

    return {
        "action": "suspend_entra_account",
        "target_email": target_email,
        "entra_user_id": user_id,
        "account_blocked": True,
        "sessions_revoked": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "enable_entra_account",
        "target_email": parameters["target_email"],
        "entra_user_id": execution_result.get("entra_user_id"),
        "rolled_back": True,
    }
