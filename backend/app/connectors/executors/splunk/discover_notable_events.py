# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_notable_events", "events": [], "count": 0}
    from ._client import get_rest_client
    query = "| inputlookup notable_xref | search status!=5 | head 100"
    async with await get_rest_client(connector) as client:
        resp = await client.post("/services/search/jobs/export", data={"search": query, "output_mode": "json"})
        resp.raise_for_status()
        events = []
        for line in resp.text.strip().split("\n"):
            import json
            try:
                obj = json.loads(line)
                if "result" in obj:
                    events.append(obj["result"])
            except Exception:
                pass
    return {"action": "discover_notable_events", "events": events[:100], "count": len(events)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
