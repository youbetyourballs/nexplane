# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_name = parameters.get('zone_name', 'smoke-test.nexplane.internal')
    private = parameters.get('private', True)
    vpc_id = parameters.get('vpc_id', None)
    region = creds.get('region', 'us-east-1') if creds else 'us-east-1'

    if not creds:
        return {
            "action": "create_route53_zone",
            "zone_id": "Z_MOCKZONE01",
            "zone_name": zone_name,
            "mock": True,
            "_auto_asset": {
                "name": zone_name,
                "asset_type": "dns_zone",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"zone_id": "Z_MOCKZONE01", "zone_name": zone_name, "private_zone": private},
                "tags": ["route53", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    ec2 = get_boto3_client(creds, 'ec2') if private else None
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {
            "Name": zone_name,
            "CallerReference": f"nexplane-{int(time.time())}",
            "HostedZoneConfig": {"Comment": "Created by Nexplane", "PrivateZone": private},
        }
        if private:
            actual_vpc_id = vpc_id
            if not actual_vpc_id:
                vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])['Vpcs']
                actual_vpc_id = vpcs[0]['VpcId'] if vpcs else None
            if actual_vpc_id:
                kwargs["VPC"] = {"VPCRegion": region, "VPCId": actual_vpc_id}
        resp = r53.create_hosted_zone(**kwargs)
        return resp['HostedZone']['Id'].split('/')[-1], resp['HostedZone']['Name']

    zone_id, zone_name_returned = await loop.run_in_executor(None, _call)
    return {
        "action": "create_route53_zone",
        "zone_id": zone_id,
        "zone_name": zone_name_returned,
        "private": private,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": zone_name_returned.rstrip('.'),
            "asset_type": "dns_zone",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "zone_id": zone_id,
                "zone_name": zone_name_returned,
                "private_zone": private,
                "region": region,
            },
            "tags": ["route53", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_route53_zone import execute as delete
    return await delete({
        "zone_id": execution_result.get('zone_id'),
        "zone_name": execution_result.get('zone_name'),
    }, [], connector)
