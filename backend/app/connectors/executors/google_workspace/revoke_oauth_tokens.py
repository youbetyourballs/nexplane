import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "revoke_oauth_tokens",
            "user_email": user_email,
            "tokens_revoked": [],
            "mock": True,
        }

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        resp = service.tokens().list(userKey=user_email).execute()
        tokens = resp.get("items", [])
        revoked = []
        errors = []
        for token in tokens:
            client_id = token["clientId"]
            snapshot = {
                "clientId": client_id,
                "displayText": token.get("displayText", ""),
                "scopes": token.get("scopes", []),
            }
            if dry_run:
                revoked.append(snapshot)
                continue
            try:
                service.tokens().delete(userKey=user_email, clientId=client_id).execute()
                revoked.append(snapshot)
            except Exception as exc:
                errors.append({"clientId": client_id, "error": str(exc)})
        return {"tokens_revoked": revoked, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "revoke_oauth_tokens",
        "user_email": user_email,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Individual OAuth grants cannot be re-issued programmatically.",
        "tokens_revoked_for_audit": execution_result.get("tokens_revoked", []),
    }
