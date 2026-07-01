# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})

    if not creds:
        return {
            "assets": [
                {
                    "name": "mock.nexplane.internal",
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": "Z_MOCK01",
                        "zone_name": "mock.nexplane.internal.",
                        "private_zone": True,
                        "record_count": 2,
                        "provider": "aws",
                    },
                    "tags": ["route53"],
                    "mock": True,
                }
            ]
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        assets = []
        paginator = r53.get_paginator('list_hosted_zones')
        for page in paginator.paginate():
            for zone in page['HostedZones']:
                zone_id = zone['Id'].split('/')[-1]
                assets.append({
                    "name": zone['Name'].rstrip('.'),
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": zone_id,
                        "zone_name": zone['Name'],
                        "private_zone": zone['Config']['PrivateZone'],
                        "record_count": zone.get('ResourceRecordSetCount', 0),
                        "region": creds.get('region', 'us-east-1'),
                        "provider": "aws",
                    },
                    "tags": ["route53", "private" if zone['Config']['PrivateZone'] else "public"],
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"assets": assets}
