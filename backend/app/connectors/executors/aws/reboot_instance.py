# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "reboot_instance", "instance_id": instance_id, "rebooted": True}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.reboot_instances(InstanceIds=[instance_id]))
    return {"action": "reboot_instance", "instance_id": instance_id, "rebooted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot is self-contained, no rollback needed"}
