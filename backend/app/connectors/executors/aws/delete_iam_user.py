# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters.get('username', '')

    if not creds:
        return {"action": "delete_iam_user", "username": username, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _call():
        # Delete all access keys
        keys = iam.list_access_keys(UserName=username)['AccessKeyMetadata']
        for k in keys:
            iam.delete_access_key(UserName=username, AccessKeyId=k['AccessKeyId'])
        # Detach all managed policies
        policies = iam.list_attached_user_policies(UserName=username)['AttachedPolicies']
        for p in policies:
            iam.detach_user_policy(UserName=username, PolicyArn=p['PolicyArn'])
        # Delete inline policies (delete_user fails if any remain)
        inline = iam.list_user_policies(UserName=username)['PolicyNames']
        for name in inline:
            iam.delete_user_policy(UserName=username, PolicyName=name)
        # Delete login profile if exists
        try:
            iam.delete_login_profile(UserName=username)
        except iam.exceptions.NoSuchEntityException:
            pass
        iam.delete_user(UserName=username)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_iam_user",
        "username": username,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_user is terminal — user data cannot be recovered"}
