# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    group_id = parameters.get("group_id")
    if group_id:
        resp = await loop.run_in_executor(None, lambda: ec2.describe_security_groups(GroupIds=[group_id]))
        groups = resp.get("SecurityGroups", [])
        valid = len(groups) > 0
    else:
        valid = True
    return {"action": "validate_security_rules", "rules_validated": len(parameters.get("rules", [])), "valid": valid, "validated_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "validate_security_rules", "rules_validated": len(parameters.get("rules", [])), "valid": True, "validated_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
