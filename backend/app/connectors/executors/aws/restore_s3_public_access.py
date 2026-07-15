# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()
    bucket_name = parameters['bucket_name']
    previous_settings = parameters['previous_settings']

    def _call():
        if previous_settings:
            s3.put_public_access_block(Bucket=bucket_name, PublicAccessBlockConfiguration=previous_settings)
        else:
            s3.delete_public_access_block(Bucket=bucket_name)

    await loop.run_in_executor(None, _call)
    return {"action": "restore_s3_public_access", "bucket_name": bucket_name, "restored_settings": previous_settings, "executed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "restore_s3_public_access", "bucket_name": parameters.get('bucket_name'), "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore action has no further rollback"}
