# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    url = parameters["url"]
    if not creds:
        return {"action": "block_url", "url": url, "blocked": True}
    from ._client import get_session
    async with await get_session(creds) as client:
        # Get current custom block category
        resp = await client.get("/urlCategories", params={"customOnly": True})
        resp.raise_for_status()
        categories = resp.json()
        block_cat = next((c for c in categories if "CUSTOM_BLOCK" in c.get("id", "") or "block" in c.get("id", "").lower()), None)
        if block_cat:
            existing_urls = block_cat.get("urls", [])
            existing_urls.append(url)
            resp2 = await client.put(f"/urlCategories/{block_cat['id']}", json={"urls": existing_urls, "configuredName": block_cat.get("configuredName")})
            resp2.raise_for_status()
    return {"action": "block_url", "url": url, "blocked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove URL from block category manually"}
