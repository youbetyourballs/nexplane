# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    dns_name = parameters["dns_name"]
    description = parameters.get("description", "Nexplane managed zone")
    if not creds:
        return {"action": "create_dns_zone", "zone_name": zone_name, "dns_name": dns_name}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        zone = svc.managedZones().create(
            project=project,
            body={
                "name": zone_name,
                "dnsName": dns_name,
                "description": description,
                "visibility": "public",
            },
        ).execute()
        return zone["name"], zone["dnsName"]

    name, created_dns_name = await loop.run_in_executor(None, _create)
    return {"action": "create_dns_zone", "zone_name": name, "dns_name": created_dns_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_dns_zone import execute as delete
    zone_name = execution_result.get("zone_name", parameters.get("zone_name"))
    return await delete({"zone_name": zone_name}, [], connector)
