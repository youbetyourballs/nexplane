from datetime import datetime, timezone

_VULNS = [
    ("legacy-app-01", "application", "vsftpd", "3.0.3", "vuln:CVE-2021-44228", "vuln-severity:critical"),
    ("legacy-app-01", "server", "legacy-app-01", None, "vuln:CVE-2023-44487", "vuln-severity:high"),
    ("payments-api-01", "server", "payments-api-01", None, "vuln:CVE-2023-20198", "vuln-severity:critical"),
    ("web-frontend-01", "application", "openssl", "1.1.1t", "vuln:CVE-2022-0778", "vuln-severity:high"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
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
                "tenable_source": "tenable_mock",
            },
        }
        if version:
            payload["asset_metadata"]["version"] = version
        results.append(payload)
    return results
