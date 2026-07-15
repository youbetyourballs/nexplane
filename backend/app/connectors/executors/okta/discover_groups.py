# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


def _mock_response():
    return {
        "action": "discover_groups",
        "assets": [
            {"id": "00g_mock001", "name": "Admins", "asset_type": "identity", "metadata": {"type": "OKTA_GROUP", "member_count": 3}},
            {"id": "00g_mock002", "name": "Everyone", "asset_type": "identity", "metadata": {"type": "BUILT_IN", "member_count": 100}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(connector) -> dict:
    from ._client import okta_headers, okta_base, get_client
    creds = getattr(connector, "credentials", {}) or {}
    base = okta_base(creds)
    headers = okta_headers(creds)
    assets = []
    url = f"{base}/groups?limit=200"
    async with await get_client(connector) as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            for group in resp.json():
                profile = group.get('profile', {})
                assets.append({
                    "id": group['id'],
                    "name": profile.get('name', group['id']),
                    "asset_type": "identity",
                    "metadata": {
                        "type": group.get('type'),
                        "description": profile.get('description'),
                    },
                })
            links = resp.headers.get('Link', '')
            url = None
            for part in links.split(','):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(';')[0].strip().strip('<>')
                    break
    return {"action": "discover_groups", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
