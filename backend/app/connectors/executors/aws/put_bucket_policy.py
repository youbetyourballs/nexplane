# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from botocore.exceptions import ClientError
from ._client import get_boto3_client


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    bucket = parameters["bucket_name"]
    policy = parameters["policy"]

    if not creds:
        return {
            "applied": True,
            "bucket_name": bucket,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    s3 = get_boto3_client(creds, "s3")

    # 1. Capture pre-state
    def _get_policy():
        try:
            resp = s3.get_bucket_policy(Bucket=bucket)
            return resp["Policy"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                return None
            raise

    prior_policy = await loop.run_in_executor(None, _get_policy)
    pre_state = {"policy": prior_policy}

    # 2. Apply new policy
    def _put():
        s3.put_bucket_policy(Bucket=bucket, Policy=policy)

    await loop.run_in_executor(None, _put)
    return {
        "applied": True,
        "bucket_name": bucket,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})
    bucket = execution_result.get("bucket_name") or parameters.get("bucket_name")

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    loop = asyncio.get_event_loop()
    s3 = get_boto3_client(creds, "s3")

    try:
        prior_policy = pre_state.get("policy")

        if prior_policy is None:
            def _delete():
                s3.delete_bucket_policy(Bucket=bucket)
            await loop.run_in_executor(None, _delete)
            return {"rolled_back": True, "bucket": bucket, "action": "deleted_policy"}
        else:
            def _restore():
                s3.put_bucket_policy(Bucket=bucket, Policy=prior_policy)
            await loop.run_in_executor(None, _restore)
            return {"rolled_back": True, "bucket": bucket, "action": "restored_prior_policy"}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
