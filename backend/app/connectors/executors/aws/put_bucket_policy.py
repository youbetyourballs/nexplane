import asyncio
import json
from datetime import datetime, timezone


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()
    bucket_name = parameters['bucket_name']
    policy = parameters['policy']

    def _call():
        s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))

    await loop.run_in_executor(None, _call)
    return {"action": "put_bucket_policy", "bucket_name": bucket_name, "executed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "put_bucket_policy", "bucket_name": parameters.get('bucket_name'), "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "bucket policy rollback requires previous policy — restore manually"}
