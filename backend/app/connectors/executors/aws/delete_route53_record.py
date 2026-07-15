# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_boto3_client


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    zone_id = parameters.get("hosted_zone_id") or parameters.get("zone_id")
    name = parameters["name"]
    record_type = parameters.get("record_type", "A")
    values = parameters.get("values", [])
    ttl = parameters.get("ttl", 300)

    if not creds:
        return {
            "deleted": True,
            "name": name,
            "record_type": record_type,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    r53 = get_boto3_client(creds, "route53")

    # 1. Capture pre-state before deletion
    def _get_record():
        resp = r53.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=name,
            StartRecordType=record_type,
            MaxItems="1",
        )
        rrsets = resp.get("ResourceRecordSets", [])
        if rrsets and rrsets[0]["Name"].rstrip(".") == name.rstrip(".") and rrsets[0]["Type"] == record_type:
            return rrsets[0]
        return None

    prior_record = await loop.run_in_executor(None, _get_record)
    pre_state = {"prior_record": prior_record} if prior_record else {}

    # 2. Mutate — build ResourceRecords from current record or parameters
    if prior_record:
        resource_records = prior_record.get("ResourceRecords", [{"Value": v} for v in values])
        effective_ttl = prior_record.get("TTL", ttl)
    else:
        resource_records = [{"Value": v} for v in values]
        effective_ttl = ttl

    def _delete():
        return r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                "Changes": [
                    {
                        "Action": "DELETE",
                        "ResourceRecordSet": {
                            "Name": name,
                            "Type": record_type,
                            "TTL": effective_ttl,
                            "ResourceRecords": resource_records,
                        },
                    }
                ]
            },
        )

    result = await loop.run_in_executor(None, _delete)
    return {
        "deleted": True,
        "name": name,
        "record_type": record_type,
        "zone_id": zone_id,
        "change_id": result.get("ChangeInfo", {}).get("Id"),
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})
    zone_id = execution_result.get("zone_id") or parameters.get("hosted_zone_id") or parameters.get("zone_id")
    name = execution_result.get("name") or parameters.get("name")
    record_type = execution_result.get("record_type") or parameters.get("record_type", "A")

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "name": name, "mock": True}

    prior_record = pre_state.get("prior_record")
    if not prior_record:
        # Fall back to parameters
        values = parameters.get("values", [])
        ttl = parameters.get("ttl", 300)
        resource_records = [{"Value": v} for v in values]
        effective_ttl = ttl
    else:
        resource_records = prior_record.get("ResourceRecords", [])
        effective_ttl = prior_record.get("TTL", 300)

    loop = asyncio.get_event_loop()
    r53 = get_boto3_client(creds, "route53")

    try:
        def _restore():
            return r53.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": name,
                                "Type": record_type,
                                "TTL": effective_ttl,
                                "ResourceRecords": resource_records,
                            },
                        }
                    ]
                },
            )

        result = await loop.run_in_executor(None, _restore)
        return {
            "rolled_back": True,
            "name": name,
            "record_type": record_type,
            "zone_id": zone_id,
            "change_id": result.get("ChangeInfo", {}).get("Id"),
        }
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
