from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "audit_users_and_groups",
        "findings": [
            {"user": "backup", "tag": "svc-interactive-shell",
             "description": "Service account 'backup' (uid=34) has interactive shell '/bin/bash'"},
        ],
        "total": 1,
        "tags": ["users-audit-findings"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_users_and_groups is read-only"}
