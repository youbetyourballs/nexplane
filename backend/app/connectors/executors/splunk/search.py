# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    query = parameters["query"]
    if not creds:
        return {"action": "search", "query": query, "results": [], "count": 0}
    from ._client import get_rest_client
    async with get_rest_client(creds) as client:
        # Create search job
        resp = await client.post("/services/search/jobs", data={"search": query, "earliest_time": parameters.get("earliest", "-24h"), "latest_time": parameters.get("latest", "now"), "output_mode": "json"})
        resp.raise_for_status()
        sid = resp.json().get("sid")
        if not sid:
            return {"action": "search", "query": query, "results": [], "count": 0}
        # Wait for job (simple polling)
        await asyncio.sleep(2)
        results_resp = await client.get(f"/services/search/jobs/{sid}/results", params={"output_mode": "json", "count": 100})
        results_resp.raise_for_status()
        results = results_resp.json().get("results", [])
    return {"action": "search", "query": query, "results": results, "count": len(results)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "search has no rollback"}
