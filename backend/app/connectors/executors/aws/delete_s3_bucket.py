import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', '')

    if not creds:
        return {"action": "delete_s3_bucket", "bucket_name": bucket_name, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
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

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_s3_bucket",
        "bucket_name": bucket_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_s3_bucket is terminal — bucket contents cannot be recovered"}
