# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _capture_alb_pre_state(elbv2, lb_arn: str) -> dict:
    """Capture full ALB configuration required to reconstitute it on rollback."""
    lb_resp = elbv2.describe_load_balancers(LoadBalancerArns=[lb_arn])
    lb = lb_resp["LoadBalancers"][0]

    listeners_resp = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
    listeners = listeners_resp.get("Listeners", [])

    # Capture target groups referenced by listeners
    tg_arns: set[str] = set()
    for listener in listeners:
        for action in listener.get("DefaultActions", []):
            if action.get("TargetGroupArn"):
                tg_arns.add(action["TargetGroupArn"])

    target_groups = []
    if tg_arns:
        tg_resp = elbv2.describe_target_groups(TargetGroupArns=list(tg_arns))
        target_groups = tg_resp.get("TargetGroups", [])

    tags_resp = elbv2.describe_tags(ResourceArns=[lb_arn])
    tags = tags_resp["TagDescriptions"][0]["Tags"] if tags_resp.get("TagDescriptions") else []

    return {
        "name": lb["LoadBalancerName"],
        "scheme": lb.get("Scheme", "internet-facing"),
        "subnets": [az["SubnetId"] for az in lb.get("AvailabilityZones", [])],
        "security_groups": lb.get("SecurityGroups", []),
        "ip_address_type": lb.get("IpAddressType", "ipv4"),
        "type": lb.get("Type", "application"),
        "tags": tags,
        "listeners": listeners,
        "target_groups": target_groups,
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    lb_arn = parameters.get("lb_arn", "")

    if not creds:
        return {"action": "delete_alb", "lb_arn": lb_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        pre_state = _capture_alb_pre_state(elbv2, lb_arn)
        elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
        waiter = elbv2.get_waiter("load_balancers_deleted")
        waiter.wait(LoadBalancerArns=[lb_arn])
        return pre_state

    pre_state = await loop.run_in_executor(None, _call)
    return {
        "action": "delete_alb",
        "lb_arn": lb_arn,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "pre_state": pre_state,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Reconstitution rollback: recreate the ALB from pre_state captured during execute()."""
    pre_state = execution_result.get("pre_state")
    if not pre_state:
        return {
            "rolled_back": False,
            "reason": "No pre_state captured in execution_result — ALB was deleted before reconstitution pattern was in place. Cannot recreate.",
        }

    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "No connector credentials available for rollback"}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _recreate():
        # Recreate the load balancer
        create_kwargs: dict = {
            "Name": pre_state["name"],
            "Subnets": pre_state["subnets"],
            "Scheme": pre_state["scheme"],
            "IpAddressType": pre_state["ip_address_type"],
            "Type": pre_state["type"],
        }
        if pre_state.get("security_groups"):
            create_kwargs["SecurityGroups"] = pre_state["security_groups"]
        if pre_state.get("tags"):
            create_kwargs["Tags"] = pre_state["tags"]

        new_lb = elbv2.create_load_balancer(**create_kwargs)
        new_arn = new_lb["LoadBalancers"][0]["LoadBalancerArn"]

        # Wait for the new ALB to be active
        waiter = elbv2.get_waiter("load_balancer_available")
        waiter.wait(LoadBalancerArns=[new_arn])

        # Restore listeners (best-effort — target groups must already exist)
        restored_listeners = []
        listener_errors = []
        for listener in pre_state.get("listeners", []):
            try:
                elbv2.create_listener(
                    LoadBalancerArn=new_arn,
                    Protocol=listener.get("Protocol", "HTTP"),
                    Port=listener.get("Port", 80),
                    DefaultActions=listener.get("DefaultActions", []),
                )
                restored_listeners.append(listener.get("Port"))
            except Exception as exc:
                listener_errors.append(f"Port {listener.get('Port')}: {exc}")

        return {
            "new_lb_arn": new_arn,
            "restored_listeners": restored_listeners,
            "listener_errors": listener_errors,
        }

    try:
        result = await loop.run_in_executor(None, _recreate)
        return {
            "rolled_back": True,
            "new_lb_arn": result["new_lb_arn"],
            "restored_listeners": result["restored_listeners"],
            "listener_errors": result["listener_errors"],
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "rolled_back": False,
            "reason": f"ALB reconstitution failed: {exc}",
        }
