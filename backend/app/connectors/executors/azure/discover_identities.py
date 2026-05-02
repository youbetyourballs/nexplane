from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_identities",
        "assets": [
            {"id": "aad-user-001", "name": "alice@example.com", "asset_type": "identity", "metadata": {"mfa_enabled": True, "account_enabled": True}},
            {"id": "aad-user-002", "name": "bob@example.com", "asset_type": "identity", "metadata": {"mfa_enabled": False, "account_enabled": True}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    import httpx
    # Acquire token via client credentials
    token_url = f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(token_url, data={
            "grant_type": "client_credentials",
            "client_id": creds['client_id'],
            "client_secret": creds['client_secret'],
            "scope": "https://graph.microsoft.com/.default",
        })
        token_resp.raise_for_status()
        token = token_resp.json()['access_token']
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("https://graph.microsoft.com/v1.0/users?$select=id,displayName,userPrincipalName,accountEnabled&$top=999", headers=headers)
        resp.raise_for_status()
        assets = []
        for user in resp.json().get('value', []):
            assets.append({
                "id": user['id'],
                "name": user.get('userPrincipalName', user['id']),
                "asset_type": "identity",
                "metadata": {
                    "account_enabled": user.get('accountEnabled'),
                    "display_name": user.get('displayName'),
                },
            })
    return {"action": "discover_identities", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
