from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_applications",
        "assets": [
            {"id": "0oa_mock001", "name": "Salesforce", "asset_type": "application", "metadata": {"status": "ACTIVE", "sign_on_mode": "SAML_2_0"}},
            {"id": "0oa_mock002", "name": "GitHub", "asset_type": "application", "metadata": {"status": "ACTIVE", "sign_on_mode": "BOOKMARK"}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    assets = []
    url = f"{base}/apps?limit=200"
    async with httpx.AsyncClient() as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            for app in resp.json():
                assets.append({
                    "id": app['id'],
                    "name": app.get('label', app['id']),
                    "asset_type": "application",
                    "metadata": {
                        "status": app.get('status'),
                        "sign_on_mode": app.get('signOnMode'),
                    },
                })
            links = resp.headers.get('Link', '')
            url = None
            for part in links.split(','):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(';')[0].strip().strip('<>')
                    break
    return {"action": "discover_applications", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
