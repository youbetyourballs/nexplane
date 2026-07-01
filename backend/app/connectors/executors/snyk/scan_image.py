# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    image = parameters["image"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "snyk_scan_image", "image": image, "findings": [], "count": 0}

    from ._client import SnykClient

    api_token = creds["api_token"]
    org_id = creds["org_id"]

    async with SnykClient(api_token, org_id) as client:
        result = await client.test_container_image(image)

    vulnerabilities = result.get("vulnerabilities", [])
    findings = [
        {
            "cve": v.get("identifiers", {}).get("CVE", [None])[0],
            "severity": v.get("severity"),
            "title": v.get("title"),
            "package_name": v.get("packageName"),
            "version": v.get("version"),
        }
        for v in vulnerabilities
    ]
    return {
        "action": "snyk_scan_image",
        "image": image,
        "findings": findings,
        "count": len(findings),
        "ok": result.get("ok", True),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan_image is read-only — no rollback needed"}
