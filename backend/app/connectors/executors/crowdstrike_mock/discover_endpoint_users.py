from datetime import datetime, timezone

_HOST_USERS = [
    ("payments-api-01", "svc-payments", "service_account"),
    ("payments-api-02", "svc-payments", "service_account"),
    ("web-frontend-01", "jsmith", "user"),
    ("db-primary-01", "svc-database", "service_account"),
    ("bastion-01", "aadmin", "admin"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if account_type in ("admin", "service_account") else "medium",
            "tags": ["crowdstrike-detected-user", f"ad-{account_type.replace('_', '-')}"],
            "asset_metadata": {
                "account_type": account_type,
                "last_seen_on_host": hostname,
                "last_seen": now,
                "crowdstrike_source": "crowdstrike_mock",
            },
        }
        for hostname, username, account_type in _HOST_USERS
    ]
