# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "stop_instance", "instance_id": instance_id, "previous_state": "running", "current_state": "stopped"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _stop_and_wait():
        resp = ec2.stop_instances(InstanceIds=[instance_id])
        change = resp['StoppingInstances'][0]
        waiter = ec2.get_waiter('instance_stopped')
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
        return change

    change = await loop.run_in_executor(None, _stop_and_wait)
    return {
        "action": "stop_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": "stopped",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.start_instance import execute as start
    return await start(parameters, [], connector)
