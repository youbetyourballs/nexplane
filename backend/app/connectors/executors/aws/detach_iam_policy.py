# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_iam_client
    iam = get_iam_client(creds)
    loop = asyncio.get_event_loop()
    principal_type = parameters['principal_type']
    principal_name = parameters['principal_name']
    policy_arn = parameters['policy_arn']

    def _call():
        if principal_type == 'user':
            iam.detach_user_policy(UserName=principal_name, PolicyArn=policy_arn)
        elif principal_type == 'group':
            iam.detach_group_policy(GroupName=principal_name, PolicyArn=policy_arn)
        elif principal_type == 'role':
            iam.detach_role_policy(RoleName=principal_name, PolicyArn=policy_arn)
        else:
            raise ValueError(f"Unknown principal_type: {principal_type}")

    await loop.run_in_executor(None, _call)
    return {"action": "detach_iam_policy", "principal_type": principal_type, "principal_name": principal_name, "policy_arn": policy_arn, "executed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "detach_iam_policy", "principal_type": parameters.get('principal_type'), "principal_name": parameters.get('principal_name'), "policy_arn": parameters.get('policy_arn'), "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use attach_iam_policy to roll back"}
