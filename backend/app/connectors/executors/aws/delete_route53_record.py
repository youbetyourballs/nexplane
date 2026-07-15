# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')
    name = parameters.get('name', '')
    record_type = parameters.get('record_type', 'A')
    values = parameters.get('values', [])
    ttl = parameters.get('ttl', 60)
    weight = parameters.get('weight', None)
    set_identifier = parameters.get('set_identifier', None)

    if not creds:
        return {"action": "delete_route53_record", "zone_id": zone_id, "name": name, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _call():
        rrs = {
            "Name": name,
            "Type": record_type,
            "TTL": ttl,
            "ResourceRecords": [{"Value": v} for v in values],
        }
        if weight is not None:
            rrs["Weight"] = weight
            rrs["SetIdentifier"] = set_identifier or name
        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Changes": [{"Action": "DELETE", "ResourceRecordSet": rrs}]},
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_route53_record",
        "zone_id": zone_id,
        "name": name,
        "type": record_type,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.upsert_route53_record import execute as upsert
    return await upsert({
        "zone_id": execution_result.get('zone_id', parameters.get('zone_id')),
        "name": execution_result.get('name', parameters.get('name')),
        "record_type": execution_result.get('type', parameters.get('record_type', 'A')),
        "values": parameters.get('values', []),
        "ttl": parameters.get('ttl', 60),
    }, [], connector)
