# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    lb_arn = parameters.get("lb_arn", "")

    if not creds:
        return {"action": "delete_alb", "lb_arn": lb_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
        waiter = elbv2.get_waiter("load_balancers_deleted")
        waiter.wait(LoadBalancerArns=[lb_arn])

    await loop.run_in_executor(None, _call)
    return {"action": "delete_alb", "lb_arn": lb_arn, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ALB deletion cannot be reversed automatically"}
