# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', '')

    if not creds:
        return {"action": "delete_s3_bucket", "bucket_name": bucket_name, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _capture():
        pre_state = {"bucket_name": bucket_name, "region": getattr(connector, "region", "us-east-1")}
        try:
            pre_state["policy"] = s3.get_bucket_policy(Bucket=bucket_name).get("Policy")
        except Exception:
            pre_state["policy"] = None
        try:
            pre_state["versioning"] = s3.get_bucket_versioning(Bucket=bucket_name)
        except Exception:
            pre_state["versioning"] = {}
        try:
            pre_state["acl"] = s3.get_bucket_acl(Bucket=bucket_name).get("Grants", [])
        except Exception:
            pre_state["acl"] = []
        try:
            pre_state["cors"] = s3.get_bucket_cors(Bucket=bucket_name).get("CORSRules", [])
        except Exception:
            pre_state["cors"] = []
        return pre_state

    def _delete():
        paginator = s3.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket_name):
            objects = []
            for v in page.get('Versions', []):
                objects.append({'Key': v['Key'], 'VersionId': v['VersionId']})
            for m in page.get('DeleteMarkers', []):
                objects.append({'Key': m['Key'], 'VersionId': m['VersionId']})
            if objects:
                s3.delete_objects(Bucket=bucket_name, Delete={'Objects': objects})
        s3.delete_bucket(Bucket=bucket_name)

    pre_state = await loop.run_in_executor(None, _capture)

    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
            pre_state,
        )
        await db.commit()

    await loop.run_in_executor(None, _delete)

    return {
        "action": "delete_s3_bucket",
        "bucket_name": bucket_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "no pre-state captured — cannot reconstitute bucket"}

    creds = getattr(connector, 'credentials', {})
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _recreate():
        bucket_name = pre_state["bucket_name"]
        region = pre_state.get("region", "us-east-1")
        create_kwargs = {"Bucket": bucket_name}
        if region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**create_kwargs)
        if pre_state.get("policy"):
            s3.put_bucket_policy(Bucket=bucket_name, Policy=pre_state["policy"])
        versioning = pre_state.get("versioning", {})
        if versioning.get("Status"):
            s3.put_bucket_versioning(Bucket=bucket_name, VersioningConfiguration={"Status": versioning["Status"]})
        if pre_state.get("cors"):
            s3.put_bucket_cors(Bucket=bucket_name, CORSConfiguration={"CORSRules": pre_state["cors"]})
        return bucket_name

    bucket_name = await loop.run_in_executor(None, _recreate)
    return {
        "rolled_back": True,
        "bucket_name": bucket_name,
        "note": "Bucket structure reconstituted. Object data is permanently lost and cannot be recovered.",
    }
