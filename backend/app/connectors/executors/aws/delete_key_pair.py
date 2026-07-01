# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    key_name = parameters.get('key_name', '')
    if not creds:
        return {"action": "delete_key_pair", "key_name": key_name, "deleted": True, "mock": True}
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.delete_key_pair(KeyName=key_name))
    return {
        "action": "delete_key_pair",
        "key_name": key_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key pair deletion is irreversible"}
