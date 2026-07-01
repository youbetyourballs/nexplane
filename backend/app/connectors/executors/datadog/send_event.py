# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    title = parameters["title"]
    if not creds:
        return {"action": "send_event", "title": title, "sent": True}
    from ._client import get_v1_client
    body = {"title": title, "text": parameters["text"]}
    if parameters.get("tags"):
        body["tags"] = parameters["tags"]
    if parameters.get("alert_type"):
        body["alert_type"] = parameters["alert_type"]
    async with get_v1_client(creds) as client:
        resp = await client.post("/events", json=body)
        resp.raise_for_status()
    return {"action": "send_event", "title": title, "sent": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "events cannot be withdrawn"}
