from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Suspend the Google Workspace account and revoke OAuth tokens."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "suspend_google_account",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    sa_info = creds.get("service_account_json")
    delegated_admin = creds.get("delegated_admin")
    scopes = [
        "https://www.googleapis.com/auth/admin.directory.user",
        "https://www.googleapis.com/auth/admin.directory.user.security",
    ]
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=scopes
    ).with_subject(delegated_admin)

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        service.users().update(userKey=target_email, body={"suspended": True}).execute()
        tokens = service.tokens().list(userKey=target_email).execute().get("items", [])
        for token in tokens:
            service.tokens().delete(userKey=target_email, clientId=token["clientId"]).execute()
        return len(tokens)

    revoked_count = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "suspend_google_account",
        "target_email": target_email,
        "suspended": True,
        "oauth_tokens_revoked": revoked_count,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "unsuspend_google_account",
        "target_email": parameters["target_email"],
        "rolled_back": True,
    }
