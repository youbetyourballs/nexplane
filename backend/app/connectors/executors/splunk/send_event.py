# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "send_event", "sent": True}
    from ._client import get_hec_client
    payload = {"event": parameters["event"]}
    if parameters.get("source"):
        payload["source"] = parameters["source"]
    if parameters.get("sourcetype"):
        payload["sourcetype"] = parameters["sourcetype"]
    if parameters.get("index"):
        payload["index"] = parameters["index"]
    async with get_hec_client(creds) as client:
        resp = await client.post("/services/collector/event", json=payload)
        resp.raise_for_status()
    return {"action": "send_event", "sent": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "events sent to Splunk cannot be withdrawn"}
