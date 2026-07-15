# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    lb_arn = parameters.get("lb_arn", "")
    protocol = parameters.get("protocol", "HTTP")
    port = parameters.get("port", 80)
    default_tg_arn = parameters.get("default_target_group_arn", "")

    if not creds:
        return {
            "action": "create_listener",
            "lb_arn": lb_arn,
            "listener_arn": "arn:aws:elasticloadbalancing:us-east-1:123:listener/app/mock/abc/def",
            "mock": True,
        }

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        if default_tg_arn:
            default_actions = [{"Type": "forward", "TargetGroupArn": default_tg_arn}]
        else:
            default_actions = [{"Type": "fixed-response", "FixedResponseConfig": {"StatusCode": "404", "ContentType": "text/plain", "MessageBody": "Not found"}}]
        resp = elbv2.create_listener(
            LoadBalancerArn=lb_arn,
            Protocol=protocol,
            Port=port,
            DefaultActions=default_actions,
        )
        return resp["Listeners"][0]

    listener = await loop.run_in_executor(None, _call)
    return {
        "action": "create_listener",
        "lb_arn": lb_arn,
        "listener_arn": listener["ListenerArn"],
        "protocol": listener.get("Protocol", protocol),
        "port": listener.get("Port", port),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    listener_arn = execution_result.get("listener_arn")
    if not listener_arn:
        return {"rolled_back": False, "reason": "no listener_arn in execution result"}
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True}
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.delete_listener(ListenerArn=listener_arn))
    return {"rolled_back": True, "listener_arn": listener_arn}
