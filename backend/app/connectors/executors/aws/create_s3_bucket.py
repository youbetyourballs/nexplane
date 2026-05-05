import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    bucket_name = parameters.get('bucket_name', 'nexplane-bucket')
    region = (creds.get('region', 'us-east-1') if creds else 'us-east-1')

    if not creds:
        return {
            "action": "create_s3_bucket",
            "bucket_name": bucket_name,
            "mock": True,
            "_auto_asset": {
                "name": bucket_name,
                "asset_type": "storage_bucket",
                "environment": "prod",
                "criticality": "medium",
                "asset_metadata": {"bucket_name": bucket_name, "region": region, "provider": "aws"},
                "tags": ["s3", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {"Bucket": bucket_name}
        if region != 'us-east-1':
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        s3.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={"TagSet": [{"Key": "ManagedBy", "Value": "nexplane"}]},
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "create_s3_bucket",
        "bucket_name": bucket_name,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": bucket_name,
            "asset_type": "storage_bucket",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {"bucket_name": bucket_name, "region": region, "provider": "aws"},
            "tags": ["s3", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_s3_bucket import execute as delete
    return await delete({"bucket_name": execution_result.get('bucket_name', parameters.get('bucket_name'))}, [], connector)
