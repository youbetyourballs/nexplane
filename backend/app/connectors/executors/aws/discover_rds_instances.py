import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_rds_instances",
        "assets": [
            {"id": "arn:aws:rds:us-east-1:123:db:prod-db", "name": "prod-db", "asset_type": "server", "metadata": {"engine": "postgres", "engine_version": "15.3", "publicly_accessible": False}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    rds = get_boto3_client(creds, 'rds')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = rds.get_paginator('describe_db_instances')
        assets = []
        for page in paginator.paginate():
            for db in page['DBInstances']:
                assets.append({
                    "id": db['DBInstanceArn'],
                    "name": db['DBInstanceIdentifier'],
                    "asset_type": "server",
                    "metadata": {
                        "engine": db.get('Engine'),
                        "engine_version": db.get('EngineVersion'),
                        "publicly_accessible": db.get('PubliclyAccessible', False),
                        "status": db.get('DBInstanceStatus'),
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_rds_instances", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
