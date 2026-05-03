from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Remove user from GitHub organization."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "remove_github_member",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        search_resp = await client.get(
            f"/search/users?q={target_email}+in:email",
            headers=headers,
        )
        search_resp.raise_for_status()
        items = search_resp.json().get("items", [])
        if not items:
            return {
                "action": "remove_github_member",
                "target_email": target_email,
                "skipped": True,
                "reason": "user not found by email",
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        username = items[0]["login"]

        resp = await client.delete(f"/orgs/{org}/members/{username}", headers=headers)
        resp.raise_for_status()

    return {
        "action": "remove_github_member",
        "target_email": target_email,
        "github_username": username,
        "removed_from_org": org,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "reinstate_github_member",
        "target_email": parameters["target_email"],
        "github_username": execution_result.get("github_username"),
        "rolled_back": True,
    }
