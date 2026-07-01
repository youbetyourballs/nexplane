# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-alarm")
    namespace = parameters.get("namespace", "oci_computeagent")
    # MQL query syntax — note the square-bracket interval and aggregation function
    query = parameters.get("query", "CpuUtilization[1m].mean() > 80")
    severity = parameters.get("severity", "CRITICAL")
    body = parameters.get("body", "CPU utilization exceeded 80%")
    destinations = parameters.get("destinations", [])   # OCI Notification topic OCIDs
    is_enabled = parameters.get("is_enabled", True)

    if not creds:
        return {
            "action": "create_alarm",
            "display_name": display_name,
            "alarm_id": "mock-alarm-id",
            "mock": True,
        }

    import oci
    from ._client import get_monitoring_client
    mon_client = get_monitoring_client(creds)
    loop = asyncio.get_running_loop()

    def _create():
        details = oci.monitoring.models.CreateAlarmDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            namespace=namespace,
            query=query,
            severity=severity,
            body=body,
            destinations=destinations,
            is_enabled=is_enabled,
        )
        return mon_client.create_alarm(create_alarm_details=details).data

    alarm = await loop.run_in_executor(None, _create)
    return {
        "action": "create_alarm",
        "display_name": display_name,
        "alarm_id": alarm.id,
        "namespace": namespace,
        "query": query,
        "severity": severity,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_alarm import execute as delete
    return await delete(
        {"alarm_id": execution_result.get("alarm_id", parameters.get("alarm_id", ""))},
        [], connector,
    )
