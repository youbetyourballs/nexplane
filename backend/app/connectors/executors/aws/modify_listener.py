# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_boto3_client


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    listener_arn = parameters["listener_arn"]
    port = parameters.get("port")
    protocol = parameters.get("protocol")
    ssl_policy = parameters.get("ssl_policy")
    default_target_group_arn = parameters.get("default_target_group_arn")

    if not creds:
        return {
            "modified": True,
            "listener_arn": listener_arn,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    elb = get_boto3_client(creds, "elbv2")

    # 1. Capture pre-state
    def _describe():
        resp = elb.describe_listeners(ListenerArns=[listener_arn])
        listeners = resp.get("Listeners", [])
        return listeners[0] if listeners else None

    prior_listener = await loop.run_in_executor(None, _describe)
    pre_state = {"listener": prior_listener} if prior_listener else {}

    # 2. Mutate
    modify_kwargs = {"ListenerArn": listener_arn}
    if port is not None:
        modify_kwargs["Port"] = port
    if protocol is not None:
        modify_kwargs["Protocol"] = protocol
    if ssl_policy is not None:
        modify_kwargs["SslPolicy"] = ssl_policy
    if default_target_group_arn is not None:
        modify_kwargs["DefaultActions"] = [
            {"Type": "forward", "TargetGroupArn": default_target_group_arn}
        ]

    def _modify():
        return elb.modify_listener(**modify_kwargs)

    await loop.run_in_executor(None, _modify)
    return {
        "modified": True,
        "listener_arn": listener_arn,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})
    listener_arn = execution_result.get("listener_arn") or parameters.get("listener_arn")

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    prior = pre_state.get("listener")
    if not prior:
        return {"rolled_back": False, "reason": "no listener data in pre_state"}

    loop = asyncio.get_event_loop()
    elb = get_boto3_client(creds, "elbv2")

    try:
        def _restore():
            restore_kwargs = {"ListenerArn": listener_arn}
            if prior.get("Protocol"):
                restore_kwargs["Protocol"] = prior["Protocol"]
            if prior.get("Port"):
                restore_kwargs["Port"] = prior["Port"]
            if prior.get("SslPolicy"):
                restore_kwargs["SslPolicy"] = prior["SslPolicy"]
            if prior.get("DefaultActions"):
                restore_kwargs["DefaultActions"] = prior["DefaultActions"]
            if prior.get("Certificates"):
                restore_kwargs["Certificates"] = prior["Certificates"]
            elb.modify_listener(**restore_kwargs)

        await loop.run_in_executor(None, _restore)
        return {"rolled_back": True, "listener_arn": listener_arn}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
