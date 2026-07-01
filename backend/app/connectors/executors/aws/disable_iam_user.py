# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_iam_client
    iam = get_iam_client(creds)
    loop = asyncio.get_event_loop()
    username = parameters['username']

    def _call():
        # Delete login profile (console access)
        try:
            iam.delete_login_profile(UserName=username)
        except iam.exceptions.NoSuchEntityException:
            pass
        # Deactivate all access keys
        keys = iam.list_access_keys(UserName=username)['AccessKeyMetadata']
        for key in keys:
            if key['Status'] == 'Active':
                iam.update_access_key(UserName=username, AccessKeyId=key['AccessKeyId'], Status='Inactive')
        return {"deactivated_keys": [k['AccessKeyId'] for k in keys if k['Status'] == 'Active']}

    result = await loop.run_in_executor(None, _call)
    return {"action": "disable_iam_user", "username": username, "executed_at": datetime.now(timezone.utc).isoformat(), **result}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "disable_iam_user", "username": parameters.get('username'), "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use enable_iam_user to roll back"}
