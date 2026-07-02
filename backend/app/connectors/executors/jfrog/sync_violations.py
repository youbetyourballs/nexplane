# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "jfrog_sync_violations", "findings": [], "count": 0}

    from ._client import JFrogClient

    filters = parameters.get("filters", {"pagination": {"order_by": "created", "limit": 100}})

    from app.tunnel.routing import http_proxy as _hp
    _proxy = await _hp(connector)
    async with JFrogClient(creds["base_url"], creds["username"], creds["password_or_token"], proxy=_proxy) as client:
        violations_raw = await client.get_violations(filters)

    findings = [
        {
            "violation_id": v.get("id"),
            "type": v.get("violation_type"),
            "severity": v.get("severity"),
            "summary": v.get("summary"),
            "cves": [c.get("cve") for c in v.get("issue_id", {}).get("cves", []) if c.get("cve")],
            "impacted_artifact": v.get("impacted_artifact"),
            "asset_ids": asset_ids,
        }
        for v in violations_raw
    ]

    return {
        "action": "jfrog_sync_violations",
        "findings": findings,
        "count": len(findings),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_violations is read-only — no rollback needed"}
