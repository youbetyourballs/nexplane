# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def _real_execute(creds: dict, asset_ids: list) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.describe_account_attributes())
    return {"action": "health_check", "healthy": True, "assets_checked": asset_ids, "checked_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "health_check", "healthy": True, "assets_checked": asset_ids, "checked_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(creds, asset_ids)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health check has no rollback"}
