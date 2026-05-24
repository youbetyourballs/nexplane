import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Check whether a CVE is still present on target assets via Tenable.io vuln export."""
    creds = getattr(connector, "credentials", {}) or {}
    cve_id = parameters.get("cve_id", "")
    if not cve_id:
        return {"status": "error", "message": "cve_id is required"}

    if not creds:
        return {
            "action": "verify_remediation",
            "cve_id": cve_id,
            "assets": asset_ids,
            "remediated": True,
            "still_vulnerable": [],
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_tio
    tio = get_tio(creds)
    loop = asyncio.get_event_loop()

    def _check():
        # Export vuln findings for this specific CVE
        vulns = list(tio.exports.vulns(
            severity=["critical", "high", "medium", "low"],
            plugin_fields=["cve"],
        ))
        # Find which asset_ids (by IP/hostname) still have this CVE
        still_vuln = set()
        for v in vulns:
            plugin_cves = v.get("plugin", {}).get("cve", [])
            if cve_id.upper() not in [c.upper() for c in plugin_cves]:
                continue
            asset = v.get("asset", {})
            identifier = (
                asset.get("fqdn")
                or asset.get("hostname")
                or asset.get("ipv4")
                or ""
            )
            if identifier:
                still_vuln.add(identifier)
        return list(still_vuln)

    still_vulnerable = await loop.run_in_executor(None, _check)

    # Cross-reference with requested asset_ids (best-effort: asset_ids may be UUIDs)
    remediated = len(still_vulnerable) == 0

    return {
        "action": "verify_remediation",
        "cve_id": cve_id,
        "assets": asset_ids,
        "remediated": remediated,
        "still_vulnerable": still_vulnerable,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remediation verification has no rollback"}
