from datetime import datetime, timezone

_USERS = [
    ("jsmith", "user", ["Domain Users", "VPN-Access"]),
    ("aadmin", "admin", ["Domain Admins", "Enterprise Admins"]),
    ("svc-backup", "service_account", ["Backup Operators"]),
    ("svc-monitoring", "service_account", ["Monitoring"]),
    ("bjones", "user", ["Domain Users"]),
    ("cwhite", "user", ["Domain Users", "Finance-Read"]),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for username, account_type, groups in _USERS:
        tag = f"ad-{account_type.replace('_', '-')}"
        results.append({
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if account_type in ("admin", "service_account") else "medium",
            "tags": [tag, "ad-user"],
            "asset_metadata": {
                "account_type": account_type,
                "groups": groups,
                "enabled": True,
                "last_login": now,
                "ad_source": "active_directory_mock",
            },
        })
    return results
