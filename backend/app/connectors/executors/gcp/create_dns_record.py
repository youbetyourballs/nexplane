# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters.get("record_type", "A")
    ttl = parameters.get("ttl", 300)
    rrdatas = parameters["rrdatas"]
    if not creds:
        return {"action": "create_dns_record", "zone_name": zone_name, "record_name": record_name}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        change = {
            "additions": [{"name": record_name, "type": record_type, "ttl": ttl, "rrdatas": rrdatas}]
        }
        svc.changes().create(project=project, managedZone=zone_name, body=change).execute()

    await loop.run_in_executor(None, _create)
    return {
        "action": "create_dns_record",
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "ttl": ttl,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_dns_record import execute as delete
    return await delete(
        {
            "zone_name": execution_result.get("zone_name", parameters["zone_name"]),
            "record_name": execution_result.get("record_name", parameters["record_name"]),
            "record_type": execution_result.get("record_type", parameters.get("record_type", "A")),
            "rrdatas": parameters.get("rrdatas", []),
        },
        [], connector,
    )
