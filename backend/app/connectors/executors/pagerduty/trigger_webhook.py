# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    routing_key = parameters["routing_key"]
    if not creds:
        return {"action": "trigger_webhook", "status": "success", "dedup_key": "mock-dedup-key"}
    payload = {
        "routing_key": routing_key,
        "event_action": parameters["event_action"],
        "payload": {
            "summary": parameters["summary"],
            "severity": parameters.get("severity", "error"),
            "source": parameters.get("source", "Nexplane"),
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=15.0)
        resp.raise_for_status()
        result = resp.json()
    return {"action": "trigger_webhook", "status": result.get("status"), "dedup_key": result.get("dedup_key")}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "webhook events cannot be withdrawn"}
