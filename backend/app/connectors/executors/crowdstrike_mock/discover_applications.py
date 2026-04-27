from datetime import datetime, timezone

_APPS = [
    ("nginx", "1.24.0", "F5 Networks"),
    ("openssl", "1.1.1t", "OpenSSL Foundation"),
    ("log4j", "2.14.1", "Apache Software Foundation"),
    ("node", "18.17.0", "Node.js Foundation"),
    ("python3", "3.10.12", "Python Software Foundation"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, version, vendor in _APPS:
        tags = ["crowdstrike-detected"]
        if name == "log4j" and version == "2.14.1":
            tags.append("vuln-severity:critical")
            tags.append("vuln:CVE-2021-44228")
        results.append({
            "name": name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "version": version,
                "vendor": vendor,
                "discovered_at": now,
                "crowdstrike_source": "crowdstrike_mock",
            },
        })
    return results
