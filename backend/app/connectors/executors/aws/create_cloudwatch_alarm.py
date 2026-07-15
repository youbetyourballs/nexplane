# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    alarm_name = parameters.get('alarm_name', 'nexplane-alarm')
    metric_name = parameters.get('metric_name', 'CPUUtilization')
    namespace = parameters.get('namespace', 'AWS/EC2')
    threshold = parameters.get('threshold', 80.0)
    comparison = parameters.get('comparison_operator', 'GreaterThanThreshold')
    evaluation_periods = parameters.get('evaluation_periods', 1)
    period = parameters.get('period', 60)
    statistic = parameters.get('statistic', 'Average')
    dimensions = parameters.get('dimensions', [])

    if not creds:
        return {
            "action": "create_cloudwatch_alarm",
            "alarm_name": alarm_name,
            "metric_name": metric_name,
            "namespace": namespace,
            "mock": True,
        }

    from ._client import get_boto3_client
    cw = get_boto3_client(creds, 'cloudwatch')
    loop = asyncio.get_event_loop()

    def _call():
        cw.put_metric_alarm(
            AlarmName=alarm_name,
            MetricName=metric_name,
            Namespace=namespace,
            Threshold=float(threshold),
            ComparisonOperator=comparison,
            EvaluationPeriods=evaluation_periods,
            Period=period,
            Statistic=statistic,
            Dimensions=dimensions,
            TreatMissingData='notBreaching',
        )

    await loop.run_in_executor(None, _call)
    return {
        "action": "create_cloudwatch_alarm",
        "alarm_name": alarm_name,
        "metric_name": metric_name,
        "namespace": namespace,
        "threshold": threshold,
        "comparison_operator": comparison,
        "dimensions": dimensions,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_cloudwatch_alarm import execute as delete
    return await delete({"alarm_name": execution_result.get('alarm_name', parameters.get('alarm_name'))}, [], connector)
