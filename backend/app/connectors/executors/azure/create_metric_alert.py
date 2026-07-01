# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    alert_name = parameters.get("alert_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    target_resource_id = parameters.get("target_resource_id", "")
    metric_name = parameters.get("metric_name", "Percentage CPU")
    threshold = parameters.get("threshold", 90)
    location = parameters.get("location", "global")

    if not creds:
        return {
            "action": "create_metric_alert",
            "alert_name": alert_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_monitor_client
    from azure.mgmt.monitor.models import (
        MetricAlertResource,
        MetricAlertSingleResourceMultipleMetricCriteria,
        MetricCriteria,
    )
    monitor = get_monitor_client(creds)
    loop = asyncio.get_running_loop()
    criteria = MetricAlertSingleResourceMultipleMetricCriteria(
        all_of=[
            MetricCriteria(
                name="HighCPU",
                metric_name=metric_name,
                metric_namespace="Microsoft.Compute/virtualMachines",
                operator="GreaterThan",
                threshold=threshold,
                time_aggregation="Average",
                criterion_type="StaticThresholdCriterion",
            )
        ]
    )
    await loop.run_in_executor(
        None,
        lambda: monitor.metric_alerts.create_or_update(
            rg, alert_name,
            MetricAlertResource(
                location=location,
                description="Nexplane smoke test alert",
                severity=3,
                enabled=True,
                scopes=[target_resource_id],
                evaluation_frequency="PT1M",
                window_size="PT5M",
                criteria=criteria,
            ),
        ),
    )
    return {
        "action": "create_metric_alert",
        "alert_name": alert_name,
        "resource_group": rg,
        "target_resource_id": target_resource_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_metric_alert import execute as delete
    return await delete(
        {
            "alert_name": execution_result.get("alert_name", parameters.get("alert_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
