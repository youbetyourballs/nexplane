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

    def _call():
        # Capture previous state
        try:
            prev = s3.get_public_access_block(Bucket=bucket_name)['PublicAccessBlockConfiguration']
        except Exception:
            prev = {}
        s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True,
            }
        )
        return {"previous_settings": prev}

    result = await loop.run_in_executor(None, _call)
    return {"action": "block_s3_public_access", "bucket_name": bucket_name, "executed_at": datetime.now(timezone.utc).isoformat(), **result}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "block_s3_public_access", "bucket_name": parameters.get('bucket_name'), "previous_settings": {}, "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "rollback handled by paired catalog action: restore_s3_public_access"}
