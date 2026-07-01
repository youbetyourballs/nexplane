# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    alarm_name = parameters.get('alarm_name', '')
    alarm_names = parameters.get('alarm_names', [alarm_name] if alarm_name else [])

    if not creds:
        return {"action": "delete_cloudwatch_alarm", "alarm_names": alarm_names, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    cw = get_boto3_client(creds, 'cloudwatch')
    loop = asyncio.get_event_loop()

    def _call():
        if alarm_names:
            cw.delete_alarms(AlarmNames=alarm_names)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_cloudwatch_alarm",
        "alarm_names": alarm_names,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.create_cloudwatch_alarm import execute as create
    return await create(parameters, [], connector)
