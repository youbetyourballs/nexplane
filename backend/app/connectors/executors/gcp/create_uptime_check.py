# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    display_name = parameters["display_name"]
    host = parameters.get("host", "google.com")
    path = parameters.get("path", "/")
    period_seconds = int(parameters.get("period_seconds", 60))
    if not creds:
        return {
            "action": "create_uptime_check",
            "check_id": "projects/mock/uptimeCheckConfigs/mock",
            "display_name": display_name,
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from google.cloud import monitoring_v3
        from google.api import monitored_resource_pb2
        from google.protobuf import duration_pb2
        client = monitoring_v3.UptimeCheckServiceClient(credentials=credentials)
        config = monitoring_v3.UptimeCheckConfig(
            display_name=display_name,
            monitored_resource=monitored_resource_pb2.MonitoredResource(
                type="uptime_url",
                labels={"host": host},
            ),
            http_check=monitoring_v3.UptimeCheckConfig.HttpCheck(
                path=path, port=80, use_ssl=False
            ),
            period=duration_pb2.Duration(seconds=period_seconds),
            timeout=duration_pb2.Duration(seconds=10),
        )
        result = client.create_uptime_check_config(
            parent=f"projects/{project}", uptime_check_config=config
        )
        return result.name

    check_id = await loop.run_in_executor(None, _create)
    return {"action": "create_uptime_check", "check_id": check_id, "display_name": display_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_uptime_check import execute as delete
    check_id = execution_result.get("check_id", "")
    if not check_id:
        return {"rolled_back": False, "reason": "no check_id in execution_result"}
    return await delete({"check_id": check_id}, [], connector)
