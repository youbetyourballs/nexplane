# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    listener_arn = parameters.get("listener_arn", "")
    port = parameters.get("port")
    protocol = parameters.get("protocol")
    default_tg_arn = parameters.get("default_target_group_arn")

    if not creds:
        return {"action": "modify_listener", "listener_arn": listener_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {"ListenerArn": listener_arn}
        if port:
            kwargs["Port"] = port
        if protocol:
            kwargs["Protocol"] = protocol
        if default_tg_arn:
            kwargs["DefaultActions"] = [{"Type": "forward", "TargetGroupArn": default_tg_arn}]
        resp = elbv2.modify_listener(**kwargs)
        return resp["Listeners"][0]

    listener = await loop.run_in_executor(None, _call)
    return {
        "action": "modify_listener",
        "listener_arn": listener_arn,
        "protocol": listener.get("Protocol"),
        "port": listener.get("Port"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "listener modification requires manual rollback — restore previous port/protocol/default action"}
