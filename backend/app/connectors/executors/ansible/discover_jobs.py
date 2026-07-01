# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_jobs", "jobs": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/jobs/", params={"page_size": 50, "order_by": "-started"})
        resp.raise_for_status()
        jobs = [{"id": j["id"], "name": j.get("name"), "status": j.get("status"), "started": j.get("started"), "finished": j.get("finished")} for j in resp.json().get("results", [])]
    return {"action": "discover_jobs", "jobs": jobs, "count": len(jobs)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
