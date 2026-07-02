# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_users",
        "assets": [
            {
                "id": "00u1mockuser000001",
                "name": "Alice Example",
                "asset_type": "identity",
                "asset_metadata": {
                    "okta_user_id": "00u1mockuser000001",
                    "login": "alice@example.com",
                    "email": "alice@example.com",
                    "first_name": "Alice",
                    "last_name": "Example",
                    "status": "ACTIVE",
                    "provider": "okta",
                },
            },
            {
                "id": "00u1mockuser000002",
                "name": "Bob Example",
                "asset_type": "identity",
                "asset_metadata": {
                    "okta_user_id": "00u1mockuser000002",
                    "login": "bob@example.com",
                    "email": "bob@example.com",
                    "first_name": "Bob",
                    "last_name": "Example",
                    "status": "ACTIVE",
                    "provider": "okta",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(connector) -> dict:
    from ._client import okta_headers, okta_base, get_client
    creds = getattr(connector, "credentials", {}) or {}
    assets = []
    url = f"{okta_base(creds)}/users?limit=200"
    async with await get_client(connector) as client:
        while url:
            resp = await client.get(url, headers=okta_headers(creds))
            resp.raise_for_status()
            for user in resp.json():
                profile = user.get("profile", {})
                display_name = profile.get("displayName") or f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip() or profile.get("login", user["id"])
                assets.append({
                    "id": user["id"],
                    "name": display_name,
                    "asset_type": "identity",
                    "asset_metadata": {
                        "okta_user_id": user["id"],
                        "login": profile.get("login"),
                        "email": profile.get("email"),
                        "first_name": profile.get("firstName"),
                        "last_name": profile.get("lastName"),
                        "status": user.get("status"),
                        "provider": "okta",
                    },
                })
            # Follow Okta pagination via Link header
            link = resp.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
            url = next_url
    return {"action": "discover_users", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
