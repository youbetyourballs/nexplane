# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    zone_id = parameters.get('zone_id', '')

    if not creds:
        return {"action": "delete_route53_zone", "zone_id": zone_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _capture():
        zone_resp = r53.get_hosted_zone(Id=zone_id)
        zone_name = zone_resp["HostedZone"]["Name"]
        paginator = r53.get_paginator("list_resource_record_sets")
        all_records = []
        for page in paginator.paginate(HostedZoneId=zone_id):
            all_records.extend(page["ResourceRecordSets"])
        return {
            "hosted_zone_id": zone_id,
            "name": zone_name,
            "record_sets": all_records,
        }

    def _delete():
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

    pre_state = await loop.run_in_executor(None, _capture)

    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
            pre_state,
        )
        await db.commit()

    await loop.run_in_executor(None, _delete)

    return {
        "action": "delete_route53_zone",
        "zone_id": zone_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "no pre-state captured — cannot reconstitute zone"}

    creds = getattr(connector, 'credentials', {})
    from ._client import get_boto3_client
    r53 = get_boto3_client(creds, 'route53')
    loop = asyncio.get_event_loop()

    def _recreate():
        new_zone = r53.create_hosted_zone(
            Name=pre_state["name"],
            CallerReference=str(_uuid.uuid4()),
        )
        new_zone_id = new_zone["HostedZone"]["Id"]
        non_default = [
            rec for rec in pre_state["record_sets"]
            if not (rec["Type"] in ("NS", "SOA") and rec["Name"] == pre_state["name"])
        ]
        if non_default:
            changes = [{"Action": "CREATE", "ResourceRecordSet": rec} for rec in non_default]
            r53.change_resource_record_sets(
                HostedZoneId=new_zone_id,
                ChangeBatch={"Changes": changes},
            )
        return new_zone_id, len(non_default)

    import uuid as _uuid
    new_zone_id, records_restored = await loop.run_in_executor(None, _recreate)
    return {
        "rolled_back": True,
        "new_hosted_zone_id": new_zone_id,
        "records_restored": records_restored,
        "note": "Zone recreated with same records. New zone ID differs from original.",
    }
