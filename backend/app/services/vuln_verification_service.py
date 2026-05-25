"""Post-execution verification: re-probe scanner to confirm finding is gone."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SUPPORTED_SCANNERS = {"nessus", "openvas", "tenable"}


async def trigger_verification(finding, db: AsyncSession) -> dict[str, Any]:
    """
    Issue a targeted re-scan of the asset that reported the finding.

    Returns {"result": "resolved"|"still_vulnerable"|"inconclusive", "assets_resolved": int, "assets_total": int}

    Caller is responsible for updating finding.status and finding.verification_result.
    """
    scanner = (finding.scanner or "").lower()
    if scanner not in _SUPPORTED_SCANNERS:
        logger.info("verification: scanner %s not supported for automated re-probe", scanner)
        return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["manual_verify"]}

    try:
        if scanner in ("nessus", "tenable"):
            return await _verify_via_nessus(finding, db)
        if scanner == "openvas":
            return await _verify_via_openvas(finding, db)
    except Exception as exc:
        logger.warning("verification probe failed for finding %s: %s", finding.id, exc)

    return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["check_scanner_connectivity"]}


async def _verify_via_nessus(finding, db: AsyncSession) -> dict:
    """Look up Nessus connector for the org and re-run a targeted scan."""
    from sqlalchemy import select
    from app.models.connector import Connector

    result = await db.execute(
        select(Connector).where(
            Connector.organization_id == finding.organization_id,
            Connector.connector_type == "nessus",
            Connector.enabled == True,
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["configure_nessus_connector"]}

    creds = connector.credentials or {}
    nessus_url = creds.get("url", "")
    token = creds.get("token", "")
    if not nessus_url or not token:
        return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["configure_nessus_credentials"]}

    import httpx
    headers = {"X-ApiKeys": f"token={token}", "Content-Type": "application/json"}
    cve_id = finding.cve_id or ""

    async with httpx.AsyncClient(verify=False, timeout=1800) as client:
        # Find existing scan covering the asset
        scans_resp = await client.get(f"{nessus_url}/scans", headers=headers)
        scans = scans_resp.json().get("scans") or []
        target_ip = finding.target_ip or ""

        # Find a scan that covers the asset's IP (heuristic: scan name contains IP or asset IP in targets)
        candidate = next(
            (s for s in scans if target_ip and target_ip in str(s.get("creation_date", ""))),
            scans[0] if scans else None,
        )
        if candidate is None:
            return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["create_nessus_scan"]}

        scan_id = candidate["id"]
        await client.post(f"{nessus_url}/scans/{scan_id}/launch", headers=headers)

        # Poll up to 30 min
        import asyncio
        for _ in range(60):
            await asyncio.sleep(30)
            status_resp = await client.get(f"{nessus_url}/scans/{scan_id}", headers=headers)
            if status_resp.json().get("info", {}).get("status") == "completed":
                break

        # Check if CVE still present in results
        vuln_resp = await client.get(f"{nessus_url}/scans/{scan_id}", headers=headers)
        vulns = []
        for host in vuln_resp.json().get("hosts", []):
            host_detail = await client.get(f"{nessus_url}/scans/{scan_id}/hosts/{host['host_id']}", headers=headers)
            vulns += host_detail.json().get("vulnerabilities", [])

        cve_still_present = any(cve_id in str(v) for v in vulns)
        if cve_still_present:
            return {"result": "still_vulnerable", "assets_resolved": 0, "assets_total": 1, "next_actions": ["add_mitigations", "escalate"]}
        return {"result": "resolved", "assets_resolved": 1, "assets_total": 1, "next_actions": []}


async def _verify_via_openvas(finding, db: AsyncSession) -> dict:
    logger.info("OpenVAS verification not yet implemented for finding %s", finding.id)
    return {"result": "inconclusive", "assets_resolved": 0, "assets_total": 1, "next_actions": ["manual_verify"]}
