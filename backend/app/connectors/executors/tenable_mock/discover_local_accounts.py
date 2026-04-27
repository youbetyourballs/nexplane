from datetime import datetime, timezone

_ACCOUNTS = [
    ("root", "legacy-app-01"),
    ("ubuntu", "payments-api-01"),
    ("deploy", "web-frontend-01"),
    ("postgres", "db-primary-01"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if username == "root" else "medium",
            "tags": ["tenable-discovered-account"],
            "asset_metadata": {
                "account_type": "local_account",
                "found_on_host": host,
                "discovered_at": now,
                "tenable_source": "tenable_mock",
            },
        }
        for username, host in _ACCOUNTS
    ]
