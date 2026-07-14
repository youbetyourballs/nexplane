# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only discovery — no state was changed"


def _mock_response():
    return {
        "action": "discover_rds_instances",
        "assets": [
            {
                "id": "arn:aws:rds:us-east-1:123:db:prod-db",
                "name": "prod-db",
                "asset_type": "database",
                "asset_metadata": {
                    "db_identifier": "prod-db",
                    "engine": "postgres",
                    "engine_version": "15.3",
                    "endpoint": "prod-db.abc123.us-east-1.rds.amazonaws.com",
                    "port": 5432,
                    "publicly_accessible": False,
                    "status": "available",
                },
            },
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
                endpoint = db.get('Endpoint', {})
                assets.append({
                    "id": db['DBInstanceArn'],
                    "name": db['DBInstanceIdentifier'],
                    "asset_type": "database",
                    "asset_metadata": {
                        "db_identifier": db['DBInstanceIdentifier'],
                        "engine": db.get('Engine'),
                        "engine_version": db.get('EngineVersion'),
                        "endpoint": endpoint.get('Address'),
                        "port": endpoint.get('Port'),
                        "publicly_accessible": db.get('PubliclyAccessible', False),
                        "status": db.get('DBInstanceStatus'),
                        "multi_az": db.get('MultiAZ', False),
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
