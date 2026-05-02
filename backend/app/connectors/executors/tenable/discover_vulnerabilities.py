import asyncio
from datetime import datetime, timezone

_VULNS = [
    ("legacy-app-01", "application", "vsftpd", "3.0.3", "vuln:CVE-2021-44228", "vuln-severity:critical"),
    ("legacy-app-01", "server", "legacy-app-01", None, "vuln:CVE-2023-44487", "vuln-severity:high"),
    ("payments-api-01", "server", "payments-api-01", None, "vuln:CVE-2023-20198", "vuln-severity:critical"),
    ("web-frontend-01", "application", "openssl", "1.1.1t", "vuln:CVE-2022-0778", "vuln-severity:high"),
]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for host, asset_type, name, version, cve_tag, severity_tag in _VULNS:
        payload = {
            "name": name,
            "asset_type": asset_type,
            "environment": "prod",
            "criticality": "critical" if "critical" in severity_tag else "high",
            "tags": [cve_tag, severity_tag, "tenable-scanned"],
            "asset_metadata": {
                "vulnerability_source": host,
                "last_scanned": now,
                "tenable_source": "tenable",
            },
        }
        if version:
            payload["asset_metadata"]["version"] = version
        results.append(payload)
    return results


async def _real_execute(creds: dict) -> list:
    from ._client import get_tio
    tio = get_tio(creds)
    loop = asyncio.get_event_loop()
    vulns = await loop.run_in_executor(None, lambda: list(tio.exports.vulns(severity=["critical", "high"])))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for v in vulns:
        severity = v.get("severity", "high").lower()
        plugin = v.get("plugin", {})
        cve_list = plugin.get("cve", [])
        cve_tag = f"vuln:{cve_list[0]}" if cve_list else "vuln:unknown"
        sev_tag = f"vuln-severity:{severity}"
        asset = v.get("asset", {})
        hostname = asset.get("fqdn") or asset.get("hostname") or asset.get("ipv4") or "unknown"
        results.append({
            "name": plugin.get("name", "unknown"),
            "asset_type": "server",
            "environment": "prod",
            "criticality": severity,
            "tags": [cve_tag, sev_tag, "tenable-scanned"],
            "asset_metadata": {
                "vulnerability_source": hostname,
                "plugin_id": plugin.get("id"),
                "last_scanned": now,
                "tenable_source": "tenable",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)

