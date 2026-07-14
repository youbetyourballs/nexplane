# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    listener_arn = parameters.get("listener_arn", "")

    if not creds:
        return {"action": "delete_listener", "listener_arn": listener_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.delete_listener(ListenerArn=listener_arn))
    return {
        "action": "delete_listener",
        "listener_arn": listener_arn,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "rollback handled by paired catalog action: create_listener"}
