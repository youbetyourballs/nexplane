# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')

    if not creds:
        return {"action": "tailscale_remove", "instance_id": instance_id, "removed": True, "mock": True}

    from ._client import get_boto3_client
    ssm = get_boto3_client(creds, 'ssm')
    loop = asyncio.get_event_loop()

    script = [
        "tailscale down || true",
        "tailscale logout || true",
    ]

    def _call():
        import time
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script},
        )
        command_id = resp['Command']['CommandId']
        for _ in range(12):
            time.sleep(5)
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
                return result
        return {"Status": "TimedOut"}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "tailscale_remove",
        "instance_id": instance_id,
        "removed": result.get('Status') == 'Success',
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "tailscale_remove has no further rollback"}
