# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', '')
    rules = parameters.get('rules', [])

    if not creds:
        return {"action": "set_s3_lifecycle", "bucket_name": bucket_name, "rules_applied": len(rules), "mock": True}

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        existing = []
        try:
            existing = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name).get('Rules', [])
        except s3.exceptions.ClientError:
            pass

        if rules:
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket_name,
                LifecycleConfiguration={"Rules": rules},
            )
        else:
            try:
                s3.delete_bucket_lifecycle(Bucket=bucket_name)
            except Exception:
                pass
        return existing

    existing_rules = await loop.run_in_executor(None, _call)
    return {
        "action": "set_s3_lifecycle",
        "bucket_name": bucket_name,
        "rules_applied": len(rules),
        "prior_rules": existing_rules,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    prior = execution_result.get('prior_rules', [])
    bucket = execution_result.get('bucket_name', parameters.get('bucket_name', ''))
    return await execute({"bucket_name": bucket, "rules": prior}, [], connector)
