# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_boto3_client


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    alarm_name = parameters["alarm_name"]

    if not creds:
        return {
            "deleted": True,
            "alarm_name": alarm_name,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    cw = get_boto3_client(creds, "cloudwatch")

    # 1. Capture pre-state
    def _describe():
        resp = cw.describe_alarms(AlarmNames=[alarm_name])
        alarms = resp.get("MetricAlarms", [])
        return alarms[0] if alarms else None

    prior_alarm = await loop.run_in_executor(None, _describe)
    pre_state = {"alarm": prior_alarm} if prior_alarm else {}

    # 2. Delete
    def _delete():
        cw.delete_alarms(AlarmNames=[alarm_name])

    await loop.run_in_executor(None, _delete)
    return {
        "deleted": True,
        "alarm_name": alarm_name,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    alarm = pre_state.get("alarm")
    if not alarm:
        return {"rolled_back": False, "reason": "no alarm data in pre_state"}

    loop = asyncio.get_event_loop()
    cw = get_boto3_client(creds, "cloudwatch")

    try:
        def _restore():
            put_kwargs = {
                "AlarmName": alarm["AlarmName"],
                "MetricName": alarm["MetricName"],
                "Namespace": alarm["Namespace"],
                "Statistic": alarm.get("Statistic", "Average"),
                "Period": alarm["Period"],
                "EvaluationPeriods": alarm["EvaluationPeriods"],
                "Threshold": alarm["Threshold"],
                "ComparisonOperator": alarm["ComparisonOperator"],
            }
            if alarm.get("AlarmDescription"):
                put_kwargs["AlarmDescription"] = alarm["AlarmDescription"]
            if alarm.get("Dimensions"):
                put_kwargs["Dimensions"] = alarm["Dimensions"]
            if alarm.get("AlarmActions"):
                put_kwargs["AlarmActions"] = alarm["AlarmActions"]
            if alarm.get("OKActions"):
                put_kwargs["OKActions"] = alarm["OKActions"]
            cw.put_metric_alarm(**put_kwargs)

        await loop.run_in_executor(None, _restore)
        return {
            "rolled_back": True,
            "alarm_name": alarm["AlarmName"],
        }
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
