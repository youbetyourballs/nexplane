# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "start_instance", "instance_id": instance_id, "previous_state": "stopped", "current_state": "running"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _start_and_wait():
        resp = ec2.start_instances(InstanceIds=[instance_id])
        change = resp['StartingInstances'][0]
        waiter = ec2.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
        return change

    change = await loop.run_in_executor(None, _start_and_wait)
    return {
        "action": "start_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": "running",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.stop_instance import execute as stop
    return await stop(parameters, [], connector)
