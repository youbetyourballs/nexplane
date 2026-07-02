# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Sync Elastic Security alerts into Nexplane findings."""
from typing import Optional

try:
    from ._client import get_elastic_client
except ImportError:
    get_elastic_client = None  # type: ignore[assignment]


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Pull security alerts from Elastic and return them as Nexplane findings.

    Parameters:
        start_time (str): ES date math, e.g. "now-24h". Default: "now-24h"
        end_time (str): ES date math, e.g. "now". Default: "now"
        min_severity (str): Optional minimum severity filter: low/medium/high/critical

    Returns dict with:
        action: "elastic_sync_alerts"
        count: number of alerts synced
        alerts: list of finding dicts
    """
    _gec = get_elastic_client
    if _gec is None:
        from ._client import get_elastic_client as _gec  # type: ignore[assignment]
    client = await _gec(connector)
    if client is None:
        return {
            "action": "elastic_sync_alerts",
            "count": 0,
            "alerts": [],
            "status": "skipped",
            "reason": "no credentials",
        }

    start_time: str = parameters.get("start_time", "now-24h")
    end_time: str = parameters.get("end_time", "now")
    min_severity: Optional[str] = parameters.get("min_severity")

    try:
        raw_alerts = client.get_alerts(
            start_time=start_time,
            end_time=end_time,
            min_severity=min_severity,
        )

        findings = []
        for alert in raw_alerts:
            finding = {
                "source": "elastic",
                "alert_id": alert.get("kibana.alert.uuid") or alert.get("_id", ""),
                "rule_name": alert.get("kibana.alert.rule.name", ""),
                "severity": alert.get("kibana.alert.severity", "unknown"),
                "status": alert.get("kibana.alert.workflow_status", "open"),
                "timestamp": alert.get("@timestamp", ""),
                "raw": alert,
            }
            findings.append(finding)

        return {
            "action": "elastic_sync_alerts",
            "count": len(findings),
            "alerts": findings,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_alerts has no rollback — read-only operation"}
