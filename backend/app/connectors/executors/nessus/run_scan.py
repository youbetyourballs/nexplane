# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Nessus Essentials — create scan, launch, poll until done, return findings."""
import time
from datetime import datetime, timezone
from typing import Optional


_POLL_INTERVAL_SEC = 30
_MAX_POLL_SEC = 1800  # 30 minutes
_DONE_STATUSES = {"completed", "cancelled", "aborted"}


def _extract_findings(scan_detail: dict) -> list:
    """Extract vulnerability findings from scan detail response."""
    findings = []
    vulnerabilities = scan_detail.get("vulnerabilities") or []
    hosts = scan_detail.get("hosts") or []

    # Build host IP lookup
    host_ip_map = {h.get("host_id", 0): h.get("hostname", "") for h in hosts}

    for vuln in vulnerabilities:
        severity_id = int(vuln.get("severity", 0))  # 0=Info, 1=Low, 2=Med, 3=High, 4=Crit
        severity_label = {0: "informational", 1: "low", 2: "medium", 3: "high", 4: "critical"}.get(severity_id, "informational")

        plugin_name = vuln.get("plugin_name", "")
        plugin_id = vuln.get("plugin_id", 0)
        count = int(vuln.get("count", 1))

        findings.append({
            "plugin_id": plugin_id,
            "name": plugin_name,
            "severity": severity_label,
            "severity_id": severity_id,
            "count": count,
            "cves": [],  # populated from plugin details if available
            "source": "nessus",
        })

    # Also extract per-host plugin hits from hosts list
    for host in hosts:
        host_ip = host.get("hostname", "")
        host_vulns = host.get("vulnerabilities") or []
        for hv in host_vulns:
            severity_id = int(hv.get("severity", 0))
            severity_label = {0: "informational", 1: "low", 2: "medium", 3: "high", 4: "critical"}.get(severity_id, "informational")
            findings.append({
                "plugin_id": hv.get("plugin_id", 0),
                "name": hv.get("plugin_name", ""),
                "severity": severity_label,
                "severity_id": severity_id,
                "host": host_ip,
                "count": hv.get("count", 1),
                "cves": [],
                "source": "nessus",
            })

    return findings


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Create a Nessus scan against target_hosts, launch it, poll until done,
    then fetch and return findings.
    """
    creds = getattr(connector, "credentials", {}) or {}
    target_hosts = parameters.get("target_hosts", "")
    scan_name = parameters.get("scan_name", "nexplane-smoke-scan")
    policy_id: Optional[int] = parameters.get("policy_id")
    poll_interval = int(parameters.get("poll_interval", _POLL_INTERVAL_SEC))
    max_wait = int(parameters.get("max_wait_seconds", _MAX_POLL_SEC))

    if not creds:
        return {
            "action": "nessus_run_scan",
            "status": "skipped",
            "reason": "no_credentials",
            "findings": [],
        }

    from ._client import NessusClient

    base_url = creds.get("base_url") or creds.get("url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")

    from app.tunnel.routing import http_proxy as _http_proxy
    _proxy = await _http_proxy(connector)
    client = NessusClient(base_url=base_url, username=username, password=password, proxy=_proxy)
    client.login()

    scan_id: Optional[int] = None

    try:
        # Create scan
        create_resp = client.create_scan(
            name=scan_name,
            targets=target_hosts,
            policy_id=policy_id,
        )
        scan_data = create_resp.get("scan", create_resp)
        scan_id = int(scan_data.get("id", 0))

        # Launch scan
        client.launch_scan(scan_id)

        # Poll until done
        deadline = time.time() + max_wait
        final_status = "unknown"
        while time.time() < deadline:
            time.sleep(poll_interval)
            detail = client.get_scan_status(scan_id)
            info = detail.get("info", {})
            status = info.get("status", "").lower()
            if status in _DONE_STATUSES:
                final_status = status
                break

        # Fetch results
        scan_detail = client.get_scan_results(scan_id)
        findings = _extract_findings(scan_detail)

        return {
            "action": "nessus_run_scan",
            "status": final_status,
            "scan_id": scan_id,
            "findings": findings,
            "finding_count": len(findings),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception:
        # Attempt cleanup on error
        if scan_id is not None:
            try:
                client.delete_scan(scan_id)
            except Exception:
                pass
        raise


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the scan created during execute."""
    creds = getattr(connector, "credentials", {}) or {}
    scan_id = execution_result.get("scan_id")

    if not creds or scan_id is None:
        return {"rolled_back": False, "reason": "nothing_to_rollback"}

    from ._client import NessusClient

    base_url = creds.get("base_url") or creds.get("url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")

    from app.tunnel.routing import http_proxy as _http_proxy
    _proxy = await _http_proxy(connector)
    client = NessusClient(base_url=base_url, username=username, password=password, proxy=_proxy)
    client.login()

    try:
        client.delete_scan(int(scan_id))
        return {"rolled_back": True, "scan_id": scan_id}
    except Exception as e:
        return {"rolled_back": False, "scan_id": scan_id, "error": str(e)}
