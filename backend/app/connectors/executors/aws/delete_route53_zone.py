# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')

    if not creds:
        return {"action": "delete_route53_zone", "zone_id": zone_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = r53.get_paginator('list_resource_record_sets')
        changes = []
        for page in paginator.paginate(HostedZoneId=zone_id):
            for rrs in page['ResourceRecordSets']:
                if rrs['Type'] in ('SOA', 'NS'):
                    continue
                changes.append({'Action': 'DELETE', 'ResourceRecordSet': rrs})
        if changes:
            r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={'Changes': changes},
            )
        r53.delete_hosted_zone(Id=zone_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_route53_zone",
        "zone_id": zone_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_route53_zone is terminal"}
