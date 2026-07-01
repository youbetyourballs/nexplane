# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    project_id = parameters["project_id"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "snyk_sync_findings", "project_id": project_id, "findings": [], "count": 0}

    from ._client import SnykClient

    api_token = creds["api_token"]
    org_id = creds["org_id"]

    async with SnykClient(api_token, org_id) as client:
        issues = await client.get_issues(org_id, project_id)

    findings = []
    for issue in issues:
        attrs = issue.get("attributes", {})
        problems = attrs.get("problems", [])
        cves = [p.get("id") for p in problems if p.get("source") == "CVE"]
        findings.append(
            {
                "issue_id": issue.get("id"),
                "title": attrs.get("title"),
                "severity": attrs.get("effective_severity_level"),
                "cves": cves,
                "asset_ids": asset_ids,
                "status": attrs.get("status"),
                "ignored": attrs.get("ignored", False),
            }
        )

    return {
        "action": "snyk_sync_findings",
        "project_id": project_id,
        "findings": findings,
        "count": len(findings),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_findings is read-only — no rollback needed"}
