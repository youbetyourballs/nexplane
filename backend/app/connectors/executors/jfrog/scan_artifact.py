from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    repo = parameters["repo"]
    path = parameters["path"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "jfrog_scan_artifact", "repo": repo, "path": path, "violations": [], "count": 0}

    from ._client import JFrogClient

    async with JFrogClient(creds["base_url"], creds["username"], creds["password_or_token"]) as client:
        scan_result = await client.scan_artifact(repo, path)
        # Xray returns violations asynchronously; fetch them
        violations_raw = await client.get_violations(
            {"filters": {"artifact": f"{repo}/{path}"}, "pagination": {"order_by": "created", "limit": 100}}
        )

    violations = [
        {
            "type": v.get("violation_type"),
            "severity": v.get("severity"),
            "summary": v.get("summary"),
            "cves": [c.get("cve") for c in v.get("issue_id", {}).get("cves", []) if c.get("cve")],
            "impacted_artifact": v.get("impacted_artifact"),
        }
        for v in violations_raw
    ]

    return {
        "action": "jfrog_scan_artifact",
        "repo": repo,
        "path": path,
        "scan_result": scan_result,
        "violations": violations,
        "count": len(violations),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan_artifact is read-only — no rollback needed"}
