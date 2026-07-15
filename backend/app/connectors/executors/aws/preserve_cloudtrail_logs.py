# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_boto3_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket = parameters["bucket"]
    prefix = parameters.get("prefix", "")
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {
            "action": "preserve_cloudtrail_logs",
            "bucket": bucket,
            "prefix": prefix,
            "objects_locked": 0,
            "simulated": True,
            "locked_at": datetime.now(timezone.utc).isoformat(),
        }

    return await _apply_legal_hold(bucket, prefix, region, "ON", creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket = execution_result.get("bucket") or parameters.get("bucket")
    prefix = execution_result.get("prefix") or parameters.get("prefix", "")
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {"rolled_back": True, "simulated": True, "bucket": bucket}

    result = await _apply_legal_hold(bucket, prefix, region, "OFF", creds)
    return {"rolled_back": True, **result}


async def _apply_legal_hold(bucket: str, prefix: str, region: str, status: str, creds: dict) -> dict:
    def _sync():
        creds_with_region = {**creds, "region": region}
        s3 = get_boto3_client(creds_with_region, "s3")
        paginator = s3.get_paginator("list_objects_v2")
        count = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                s3.put_object_legal_hold(
                    Bucket=bucket,
                    Key=obj["Key"],
                    LegalHold={"Status": status},
                )
                count += 1
        return count

    count = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "preserve_cloudtrail_logs",
        "bucket": bucket,
        "prefix": prefix,
        "objects_locked": count,
        "legal_hold_status": status,
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
