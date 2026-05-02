import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_users",
        "assets": [
            {"id": "00u_mock001", "name": "alice@example.com", "asset_type": "identity", "metadata": {"status": "ACTIVE", "login": "alice@example.com", "mfa_enrolled": True}},
            {"id": "00u_mock002", "name": "bob@example.com", "asset_type": "identity", "metadata": {"status": "ACTIVE", "login": "bob@example.com", "mfa_enrolled": False}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    assets = []
    url = f"{base}/users?limit=200"
    async with httpx.AsyncClient() as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            for user in resp.json():
                profile = user.get('profile', {})
                assets.append({
                    "id": user['id'],
                    "name": profile.get('login', user['id']),
                    "asset_type": "identity",
                    "metadata": {
                        "status": user.get('status'),
                        "login": profile.get('login'),
                        "email": profile.get('email'),
                        "first_name": profile.get('firstName'),
                        "last_name": profile.get('lastName'),
                    },
                })
            links = resp.headers.get('Link', '')
            url = None
            for part in links.split(','):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(';')[0].strip().strip('<>')
                    break
    return {"action": "discover_users", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
