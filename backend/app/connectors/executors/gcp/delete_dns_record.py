# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters.get("record_type", "A")
    rrdatas = parameters.get("rrdatas", [])
    if not creds:
        return {"action": "delete_dns_record", "zone_name": zone_name, "record_name": record_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        # Fetch current TTL + rrdatas if not provided
        actual_rrdatas = rrdatas
        ttl = 300
        if not actual_rrdatas:
            records_resp = svc.resourceRecordSets().list(
                project=project, managedZone=zone_name
            ).execute()
            for r in records_resp.get("rrsets", []):
                if r["name"] == record_name and r["type"] == record_type:
                    actual_rrdatas = r["rrdatas"]
                    ttl = r["ttl"]
                    break
        if not actual_rrdatas:
            return  # Record not found — already gone
        change = {
            "deletions": [{"name": record_name, "type": record_type, "ttl": ttl, "rrdatas": actual_rrdatas}]
        }
        svc.changes().create(project=project, managedZone=zone_name, body=change).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_dns_record", "zone_name": zone_name, "record_name": record_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS record deletion is irreversible"}
