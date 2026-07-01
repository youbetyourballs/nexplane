# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""OpenVAS — import scan findings into Nexplane as vulnerabilities linked to assets."""
from datetime import datetime, timezone


_SEVERITY_LABEL = {
    (0.0, 3.9): "low",
    (4.0, 6.9): "medium",
    (7.0, 8.9): "high",
    (9.0, 10.0): "critical",
}


def _severity_label(score: float) -> str:
    for (lo, hi), label in _SEVERITY_LABEL.items():
        if lo <= score <= hi:
            return label
    return "informational"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Takes scan findings (from run_scan output) and upserts them as
    Nexplane vulnerability findings linked to the relevant assets.

    parameters:
        findings: list[dict]  — output from openvas_run_scan
        scan_report_id: str   — OpenVAS report ID (for deduplication)
        source_connector_id: str (optional)
    """
    findings_raw: list = parameters.get("findings", [])
    report_id: str = parameters.get("scan_report_id", "")
    source_connector_id: str = parameters.get("source_connector_id", "")

    imported = []
    skipped = 0

    for f in findings_raw:
        if f.get("error") or not f.get("name"):
            skipped += 1
            continue

        cves = f.get("cves", [])
        severity_score = float(f.get("severity", 0.0))
        severity = _severity_label(severity_score)

        imported.append({
            "title": f.get("name", ""),
            "description": f.get("description", ""),
            "severity": severity,
            "cvss_score": severity_score,
            "cve_ids": cves,
            "host": f.get("host", ""),
            "port": f.get("port", ""),
            "oid": f.get("oid", ""),
            "source": "openvas",
            "report_id": report_id,
            "source_connector_id": source_connector_id,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    return {
        "action": "openvas_import_findings",
        "imported_count": len(imported),
        "skipped_count": skipped,
        "findings": imported,
        "asset_ids": [str(a) for a in asset_ids],
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "finding_import_is_not_reversible"}
