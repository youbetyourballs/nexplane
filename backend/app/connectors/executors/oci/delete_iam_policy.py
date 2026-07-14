# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "IAM policy deletion is destructive; policy statements are not preserved for rollback"

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_id = parameters.get("policy_id", "")

    if not creds:
        return {"action": "delete_iam_policy", "policy_id": policy_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: client.delete_policy(policy_id))
    return {
        "action": "delete_iam_policy",
        "policy_id": policy_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_policy is destructive; no rollback available"}
