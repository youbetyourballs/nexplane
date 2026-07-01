# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""CISA KEV check, EPSS enrichment, PoC discovery, and exploitability state transitions."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_kev_cache: Optional[dict] = None

_EPSS_API_URL = "https://api.first.org/data/v1/epss"
# In-memory cache: cve_id → {"score": float, "percentile": float, "fetched_at": datetime}
_epss_cache: dict[str, dict] = {}

_SEVERITY_ORDER = ["informational", "low", "medium", "high", "critical"]


def check_cisa_kev(cve_id: str) -> bool:
    """Return True if cve_id appears in the cached CISA KEV catalog."""
    if _kev_cache is None:
        return False
    return any(
        v.get("cveID") == cve_id
        for v in _kev_cache.get("vulnerabilities", [])
    )


async def refresh_kev_cache() -> None:
    """Fetch CISA KEV JSON and update the module-level cache. Called by APScheduler daily."""
    global _kev_cache
    try:
        resp = httpx.get(_CISA_KEV_URL, timeout=30)
        resp.raise_for_status()
        _kev_cache = resp.json()
        logger.info("CISA KEV cache refreshed: %d entries", len(_kev_cache.get("vulnerabilities", [])))
    except Exception as exc:
        logger.warning("Failed to refresh CISA KEV cache: %s", exc)


def get_epss_score(cve_id: str) -> Optional[dict]:
    """Return cached EPSS data for cve_id, or None if not cached yet."""
    return _epss_cache.get(cve_id)


async def fetch_epss(cve_id: str) -> Optional[dict]:
    """Fetch EPSS score for a single CVE from FIRST.org. Caches the result in-memory."""
    if not cve_id or not cve_id.upper().startswith("CVE-"):
        return None
    if cve_id in _epss_cache:
        return _epss_cache[cve_id]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_EPSS_API_URL, params={"cve": cve_id})
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        entry = data[0]
        result = {
            "score": float(entry.get("epss", 0)),
            "percentile": float(entry.get("percentile", 0)),
            "fetched_at": datetime.now(timezone.utc),
        }
        _epss_cache[cve_id] = result
        return result
    except Exception as exc:
        logger.debug("EPSS fetch failed for %s: %s", cve_id, exc)
        return None


async def enrich_finding_epss(finding) -> None:
    """Fetch and apply EPSS score to a finding if it has a CVE ID. No-op if no CVE or fetch fails."""
    if not finding.cve_id:
        return
    epss = await fetch_epss(finding.cve_id)
    if epss:
        finding.epss_score = epss["score"]
        finding.epss_percentile = epss["percentile"]
        finding.epss_fetched_at = epss["fetched_at"]


def _escalate_severity(current: str) -> str:
    idx = _SEVERITY_ORDER.index(current) if current in _SEVERITY_ORDER else 2
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def _downgrade_severity(current: str) -> str:
    idx = _SEVERITY_ORDER.index(current) if current in _SEVERITY_ORDER else 2
    return _SEVERITY_ORDER[max(idx - 1, 0)]


def apply_poc_result(finding, result: str, poc_source: Optional[str], poc_ref: Optional[str]) -> None:
    """
    Apply a PoC run result to a finding in-place.

    result: "exploited" | "not_exploited" | "inconclusive" | "no_poc_available"
    Modifies finding.status, finding.exploitability_result, finding.severity, finding.poc_source, finding.poc_ref.
    Caller must flush/commit the DB session.
    """
    finding.exploitability_result = result
    finding.poc_source = poc_source
    finding.poc_ref = poc_ref

    if result == "exploited":
        finding.status = "actionable"
        finding.severity = _escalate_severity(finding.severity)
    elif result == "not_exploited":
        finding.status = "actionable"
        finding.severity = _downgrade_severity(finding.severity)
    # inconclusive / no_poc_available: no status change, no SLA change


def discover_poc(cve_id: str) -> dict:
    """
    Check Metasploit module index and ExploitDB for a known PoC.
    Returns {"source": str, "confidence": float, "poc_ref": str} or {"source": None} if none found.
    """
    if check_cisa_kev(cve_id):
        return {"source": "cisa_kev", "confidence": 1.0, "poc_ref": cve_id}

    try:
        resp = httpx.get(
            "https://raw.githubusercontent.com/rapid7/metasploit-framework/master/db/modules_metadata_base.json",
            timeout=15,
        )
        if resp.status_code == 200:
            modules = resp.json()
            for mod_name, mod_data in modules.items():
                if cve_id in str(mod_data.get("references", [])):
                    return {"source": "metasploit", "confidence": 0.9, "poc_ref": mod_name}
    except Exception:
        pass

    try:
        search_resp = httpx.get(
            f"https://www.exploit-db.com/search?cve={cve_id}&type=exploits",
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if search_resp.status_code == 200:
            data = search_resp.json()
            if data.get("recordsTotal", 0) > 0:
                first = data.get("data", [{}])[0]
                return {"source": "exploitdb", "confidence": 0.7, "poc_ref": str(first.get("id", ""))}
    except Exception:
        pass

    return {"source": None, "confidence": 0.0, "poc_ref": ""}
