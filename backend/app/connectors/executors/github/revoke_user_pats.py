import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "revoke_user_pats", "username": username, "pats_revoked": [], "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        org = creds["org"]
        resp = httpx.get(
            f"https://api.github.com/orgs/{org}/personal-access-tokens",
            params={"owner": username},
            headers=headers,
        )
        resp.raise_for_status()
        pats = resp.json()
        revoked = []
        errors = []
        for pat in pats:
            pat_id = pat["id"]
            meta = {"id": pat_id, "name": pat.get("name"), "last_used": pat.get("last_used_at")}
            if dry_run:
                revoked.append(meta)
                continue
            del_resp = httpx.delete(
                f"https://api.github.com/orgs/{org}/personal-access-tokens/{pat_id}",
                headers=headers,
            )
            if del_resp.status_code in (204, 404):
                revoked.append(meta)
            else:
                errors.append({"id": pat_id, "status": del_resp.status_code})
        return {"pats_revoked": revoked, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "revoke_user_pats",
        "username": username,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "PATs cannot be re-issued programmatically.",
        "pats_revoked_for_audit": execution_result.get("pats_revoked", []),
    }
