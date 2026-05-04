import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_s3_buckets",
        "assets": [
            {
                "id": "arn:aws:s3:::my-public-bucket",
                "name": "my-public-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-public-bucket",
                    "public_access_blocked": False,
                    "region": "us-east-1",
                    "provider": "aws",
                },
            },
            {
                "id": "arn:aws:s3:::my-private-bucket",
                "name": "my-private-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-private-bucket",
                    "public_access_blocked": True,
                    "region": "us-west-2",
                    "provider": "aws",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    s3 = get_boto3_client(creds, 's3')
    loop = asyncio.get_event_loop()

    def _call():
        response = s3.list_buckets()
        assets = []
        for bucket in response.get('Buckets', []):
            name = bucket['Name']
            try:
                pab = s3.get_public_access_block(Bucket=name)['PublicAccessBlockConfiguration']
                blocked = all([
                    pab.get('BlockPublicAcls'), pab.get('IgnorePublicAcls'),
                    pab.get('BlockPublicPolicy'), pab.get('RestrictPublicBuckets'),
                ])
            except Exception:
                blocked = False
            try:
                loc = s3.get_bucket_location(Bucket=name)['LocationConstraint'] or 'us-east-1'
            except Exception:
                loc = 'unknown'
            assets.append({
                "id": f"arn:aws:s3:::{name}",
                "name": name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": name,
                    "public_access_blocked": blocked,
                    "region": loc,
                    "provider": "aws",
                },
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_s3_buckets", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
