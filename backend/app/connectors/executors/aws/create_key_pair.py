# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    key_name = parameters.get('key_name', 'nexplane-key')
    if not creds:
        return {
            "action": "create_key_pair",
            "key_name": key_name,
            "key_fingerprint": "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99",
            "mock": True,
            "_auto_asset": {
                "name": key_name,
                "asset_type": "key_pair",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {
                    "key_name": key_name,
                    "key_fingerprint": "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99",
                    "region": "us-east-1",
                    "provider": "aws",
                },
                "tags": ["nexplane-managed"],
            },
        }
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()

    def _call():
        return ec2.create_key_pair(
            KeyName=key_name,
            TagSpecifications=[{
                "ResourceType": "key-pair",
                "Tags": [{"Key": "ManagedBy", "Value": "nexplane"}],
            }],
        )

    resp = await loop.run_in_executor(None, _call)
    return {
        "action": "create_key_pair",
        "key_name": key_name,
        "key_fingerprint": resp.get('KeyFingerprint', ''),
        "private_key_material": resp.get('KeyMaterial', ''),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": key_name,
            "asset_type": "key_pair",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "key_name": key_name,
                "key_fingerprint": resp.get('KeyFingerprint', ''),
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_key_pair import execute as delete
    return await delete({"key_name": execution_result.get('key_name', parameters.get('key_name'))}, [], connector)
