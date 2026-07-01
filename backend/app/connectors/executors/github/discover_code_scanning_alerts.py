# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_code_scanning_alerts", "alerts": [], "count": 0}
    from ._client import get_client
    org = creds["org"]
    async with get_client(creds) as client:
        resp = await client.get(f"/orgs/{org}/code-scanning/alerts", params={"state": "open", "per_page": 100})
        resp.raise_for_status()
        alerts = [{"number": a["number"], "rule_id": a.get("rule", {}).get("id"), "severity": a.get("rule", {}).get("severity"), "repo": a.get("repository", {}).get("name")} for a in resp.json()]
    return {"action": "discover_code_scanning_alerts", "alerts": alerts, "count": len(alerts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
