# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    if not creds:
        return {"action": "delete_dns_zone", "zone_name": zone_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        # Delete all record sets except SOA and NS before deleting zone
        page_token = None
        while True:
            kwargs = {"project": project, "managedZone": zone_name}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.resourceRecordSets().list(**kwargs).execute()
            records = [r for r in resp.get("rrsets", []) if r["type"] not in ("SOA", "NS")]
            if records:
                changes = {"deletions": records}
                svc.changes().create(project=project, managedZone=zone_name, body=changes).execute()
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        svc.managedZones().delete(project=project, managedZone=zone_name).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_dns_zone", "zone_name": zone_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS zone deletion is irreversible"}
