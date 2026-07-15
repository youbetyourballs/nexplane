# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    display_name = parameters["display_name"]
    condition_threshold = float(parameters.get("condition_threshold", 0.9))
    duration_seconds = int(parameters.get("duration_seconds", 60))
    if not creds:
        return {
            "action": "create_alert_policy",
            "policy_name": "projects/mock/alertPolicies/mock",
            "display_name": display_name,
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from google.cloud import monitoring_v3
        client = monitoring_v3.AlertPolicyServiceClient(credentials=credentials)
        policy = monitoring_v3.AlertPolicy(
            display_name=display_name,
            conditions=[
                monitoring_v3.AlertPolicy.Condition(
                    display_name="CPU utilization high",
                    condition_threshold=monitoring_v3.AlertPolicy.Condition.MetricThreshold(
                        filter='resource.type = "gce_instance" AND metric.type = "compute.googleapis.com/instance/cpu/utilization"',
                        comparison=monitoring_v3.ComparisonType.COMPARISON_GT,
                        threshold_value=condition_threshold,
                        duration={"seconds": duration_seconds},
                        aggregations=[
                            monitoring_v3.Aggregation(
                                alignment_period={"seconds": 60},
                                per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                            )
                        ],
                    ),
                )
            ],
            combiner=monitoring_v3.AlertPolicy.ConditionCombinerType.AND,
            enabled=True,
        )
        result = client.create_alert_policy(
            name=f"projects/{project}", alert_policy=policy
        )
        return result.name

    policy_name = await loop.run_in_executor(None, _create)
    return {"action": "create_alert_policy", "policy_name": policy_name, "display_name": display_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_alert_policy import execute as delete
    policy_name = execution_result.get("policy_name", "")
    if not policy_name:
        return {"rolled_back": False, "reason": "no policy_name in execution_result"}
    return await delete({"policy_name": policy_name}, [], connector)
