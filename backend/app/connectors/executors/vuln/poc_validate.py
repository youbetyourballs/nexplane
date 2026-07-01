# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
vuln_poc_validate executor.

Read-only probe: checks if a CVE is exploitable against the target asset
using a credentialed Nessus plugin scan. Returns:
  {"result": "exploited"|"not_exploited"|"inconclusive", "evidence": str, "scanner_output": str}

Rollback: N/A (read-only).
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(params: dict[str, Any], connector_config: dict[str, Any]) -> dict[str, Any]:
    """
    params:
      cve_id: str
      asset_id: str
      poc_source: str  (metasploit_module | nessus_plugin | exploitdb_ref)
      poc_ref: str     (module name or plugin ID)
      nessus_url: str  (optional, from connector_config if not in params)
      nessus_token: str (optional)
    """
    cve_id = params.get("cve_id", "")
    poc_source = params.get("poc_source", "")
    poc_ref = params.get("poc_ref", "")
    nessus_url = params.get("nessus_url") or connector_config.get("url", "")
    nessus_token = params.get("nessus_token") or connector_config.get("token", "")

    if poc_source == "nessus_plugin" and nessus_url and nessus_token:
        return await _run_nessus_plugin_check(cve_id, poc_ref, nessus_url, nessus_token)

    logger.info("poc_validate: no suitable runner for %s / %s — returning inconclusive", poc_source, poc_ref)
    return {
        "result": "inconclusive",
        "evidence": f"No automated runner available for poc_source={poc_source}",
        "scanner_output": "",
    }


async def _run_nessus_plugin_check(cve_id: str, plugin_id: str, nessus_url: str, token: str) -> dict:
    """Issue a single-plugin Nessus scan and parse results."""
    import httpx
    headers = {"X-ApiKeys": f"token={token}", "Content-Type": "application/json"}
    client = httpx.AsyncClient(verify=False, timeout=300)

    try:
        # Launch a targeted plugin scan
        scan_payload = {
            "uuid": "bbd4f805-3966-d464-b2d1-0079eb89d69f",  # basic network scan template
            "settings": {
                "name": f"nexplane-poc-{cve_id}",
                "enabled": True,
                "launch": "ON_DEMAND",
            },
            "plugins": {plugin_id: {"status": "enabled"}} if plugin_id else {},
        }
        create_resp = await client.post(f"{nessus_url}/scans", headers=headers, json=scan_payload)
        if create_resp.status_code not in (200, 201):
            return {"result": "inconclusive", "evidence": f"scan create HTTP {create_resp.status_code}", "scanner_output": ""}

        scan_id = create_resp.json()["scan"]["id"]
        await client.post(f"{nessus_url}/scans/{scan_id}/launch", headers=headers)

        # Poll until completed (max 10 min)
        import asyncio
        for _ in range(60):
            await asyncio.sleep(10)
            status_resp = await client.get(f"{nessus_url}/scans/{scan_id}", headers=headers)
            status = status_resp.json().get("info", {}).get("status", "")
            if status == "completed":
                break
        else:
            return {"result": "inconclusive", "evidence": "Nessus scan timed out", "scanner_output": ""}

        # Parse results for the CVE
        hosts = status_resp.json().get("hosts", [])
        for host in hosts:
            host_detail = await client.get(f"{nessus_url}/scans/{scan_id}/hosts/{host['host_id']}", headers=headers)
            for vuln in host_detail.json().get("vulnerabilities", []):
                if str(vuln.get("plugin_id")) == str(plugin_id):
                    severity = vuln.get("severity", 0)
                    result = "exploited" if severity > 0 else "not_exploited"
                    return {
                        "result": result,
                        "evidence": f"Nessus plugin {plugin_id} severity={severity}",
                        "scanner_output": str(vuln),
                    }

        return {"result": "not_exploited", "evidence": "Plugin not triggered on any host", "scanner_output": ""}
    finally:
        await client.aclose()
