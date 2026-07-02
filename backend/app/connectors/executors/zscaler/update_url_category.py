# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    category_id = parameters["category_id"]
    if not creds:
        return {"action": "update_url_category", "category_id": category_id, "updated": True}
    from ._client import get_session
    async with await get_session(creds, connector) as client:
        resp = await client.get(f"/urlCategories/{category_id}")
        resp.raise_for_status()
        current = resp.json()
        urls = current.get("urls", [])
        if parameters.get("urls_to_add"):
            urls = list(set(urls + parameters["urls_to_add"]))
        if parameters.get("urls_to_remove"):
            urls = [u for u in urls if u not in parameters["urls_to_remove"]]
        resp2 = await client.put(f"/urlCategories/{category_id}", json={"urls": urls, "configuredName": current.get("configuredName")})
        resp2.raise_for_status()
    return {"action": "update_url_category", "category_id": category_id, "updated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous URL category state not captured — restore manually"}
