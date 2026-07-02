# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""OpenVAS — create target + task, launch scan, poll until done, return CVE findings."""
import time
from datetime import datetime, timezone
from typing import Optional


# Full-and-Fast scan config UUID in Greenbone CE
_DEFAULT_CONFIG_ID = "daba56c8-73ec-11df-a475-002264764cea"
# Poll interval and timeout
_POLL_INTERVAL_SEC = 30
_MAX_POLL_SEC = 1800  # 30 minutes


def _extract_findings(report: dict) -> list:
    """Pull CVE list with severity from a report response."""
    findings = []
    results_data = report.get("results") or report.get("report", {}).get("results", {})
    if isinstance(results_data, dict):
        results_list = results_data.get("result", [])
        if isinstance(results_list, dict):
            results_list = [results_list]
    elif isinstance(results_data, list):
        results_list = results_data
    else:
        results_list = []

    for r in results_list:
        severity_raw = r.get("severity") or r.get("cvss_base", "0")
        try:
            severity = float(severity_raw)
        except (ValueError, TypeError):
            severity = 0.0

        cves = []
        nvt = r.get("nvt") or {}
        refs = nvt.get("refs", {}).get("ref", [])
        if isinstance(refs, dict):
            refs = [refs]
        for ref in refs:
            if ref.get("type") == "cve" or ref.get("id", "").startswith("CVE-"):
                cves.append(ref.get("id", ""))

        findings.append({
            "oid": nvt.get("oid", ""),
            "name": nvt.get("name", r.get("name", "")),
            "severity": severity,
            "host": r.get("host", {}).get("ip", "") if isinstance(r.get("host"), dict) else r.get("host", ""),
            "port": r.get("port", ""),
            "cves": cves,
            "description": r.get("description", "")[:500] if r.get("description") else "",
        })
    return findings


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Launch a full OpenVAS scan against target_hosts.
    Polls until done (up to 30 min), returns findings list.
    """
    creds = getattr(connector, "credentials", {}) or {}
    target_hosts = parameters.get("target_hosts", "")
    scan_name = parameters.get("scan_name", "nexplane-smoke-scan")
    config_id = parameters.get("config_id", _DEFAULT_CONFIG_ID)
    poll_interval = int(parameters.get("poll_interval", _POLL_INTERVAL_SEC))
    max_wait = int(parameters.get("max_wait_seconds", _MAX_POLL_SEC))

    if not creds:
        # Mock response when no credentials
        return {
            "action": "openvas_run_scan",
            "status": "skipped",
            "reason": "no_credentials",
            "findings": [],
        }

    from ._client import OpenVASClient
    from app.tunnel.routing import http_proxy as _http_proxy

    base_url = creds.get("base_url") or creds.get("url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")
    _proxy = await _http_proxy(connector)

    client = OpenVASClient(base_url=base_url, username=username, password=password, proxy=_proxy)
    client.authenticate()

    target_id: Optional[str] = None
    task_id: Optional[str] = None
    report_id: Optional[str] = None

    try:
        # Create target
        target_resp = client.create_target(name=f"{scan_name}-target", hosts=target_hosts)
        target_id = target_resp.get("id") or target_resp.get("target", {}).get("id", "")

        # Create task
        task_resp = client.create_task(name=scan_name, target_id=target_id, config_id=config_id)
        task_id = task_resp.get("id") or task_resp.get("task", {}).get("id", "")

        # Start task
        start_resp = client.start_task(task_id)
        report_id = (
            start_resp.get("report_id")
            or start_resp.get("data", {}).get("report_id", "")
        )

        # Poll until done
        deadline = time.time() + max_wait
        final_status = "unknown"
        while time.time() < deadline:
            time.sleep(poll_interval)
            task_detail = client.get_task_status(task_id)
            task_obj = task_detail.get("task") or task_detail
            status = task_obj.get("status", "")
            if status in ("Done", "Stopped", "Error", "done", "stopped", "error"):
                final_status = status
                # Prefer report_id from task status response
                last_report = task_obj.get("last_report") or task_obj.get("report", {})
                if isinstance(last_report, dict):
                    report_id = last_report.get("report", {}).get("id", report_id) or report_id
                break

        # Fetch findings
        findings: list = []
        if report_id:
            try:
                report = client.get_report(report_id)
                findings = _extract_findings(report)
            except Exception as e:
                findings = [{"error": str(e)}]

        return {
            "action": "openvas_run_scan",
            "status": final_status,
            "target_id": target_id,
            "task_id": task_id,
            "report_id": report_id,
            "findings": findings,
            "finding_count": len([f for f in findings if not f.get("error")]),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception:
        # Cleanup on error
        try:
            if task_id:
                client.delete_task(task_id)
        except Exception:
            pass
        try:
            if target_id:
                client.delete_target(target_id)
        except Exception:
            pass
        raise


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete task and target created during execute."""
    creds = getattr(connector, "credentials", {}) or {}
    task_id = execution_result.get("task_id", "")
    target_id = execution_result.get("target_id", "")

    if not creds or (not task_id and not target_id):
        return {"rolled_back": False, "reason": "nothing_to_rollback"}

    from ._client import OpenVASClient
    from app.tunnel.routing import http_proxy as _http_proxy

    base_url = creds.get("base_url") or creds.get("url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")
    _proxy = await _http_proxy(connector)

    client = OpenVASClient(base_url=base_url, username=username, password=password, proxy=_proxy)
    client.authenticate()

    errors = []
    if task_id:
        try:
            client.delete_task(task_id)
        except Exception as e:
            errors.append(f"delete_task: {e}")
    if target_id:
        try:
            client.delete_target(target_id)
        except Exception as e:
            errors.append(f"delete_target: {e}")

    return {
        "rolled_back": len(errors) == 0,
        "task_id": task_id,
        "target_id": target_id,
        "errors": errors,
    }
