async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    if not creds:
        return {"action": "enable_dependabot", "repo": repo, "enabled": True}
    from ._client import get_client
    org = creds["org"]
    async with get_client(creds) as client:
        resp = await client.put(f"/repos/{org}/{repo}/vulnerability-alerts")
        resp.raise_for_status()
    return {"action": "enable_dependabot", "repo": repo, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable Dependabot manually if needed"}
