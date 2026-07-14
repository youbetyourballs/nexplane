# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "RDS instance was deleted without a final snapshot — data cannot be recovered; enable final_snapshot in parameters before deletion"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    db_id = parameters.get('db_instance_identifier', '')

    if not creds:
        return {"action": "delete_rds_instance", "db_instance_identifier": db_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        rds.delete_db_instance(
            DBInstanceIdentifier=db_id,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True,
        )
        waiter = rds.get_waiter('db_instance_deleted')
        waiter.wait(
            DBInstanceIdentifier=db_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 40},
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_rds_instance",
        "db_instance_identifier": db_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": ROLLBACK_REASON,
    }
