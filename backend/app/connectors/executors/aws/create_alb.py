import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-alb")
    scheme = parameters.get("scheme", "internet-facing")
    lb_type = parameters.get("lb_type", "application")
    subnets = parameters.get("subnets", [])
    security_groups = parameters.get("security_groups", [])

    if not creds:
        return {
            "action": "create_alb",
            "name": name,
            "lb_arn": f"arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/{name}/mock",
            "dns_name": f"{name}-123.us-east-1.elb.amazonaws.com",
            "mock": True,
            "_auto_asset": {
                "name": name,
                "asset_type": "load_balancer",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {"lb_arn": f"mock-{name}", "lb_type": lb_type, "provider": "aws"},
                "tags": ["alb", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = {
            "Name": name,
            "Scheme": scheme,
            "Type": lb_type,
            "Subnets": subnets,
        }
        if security_groups and lb_type == "application":
            kwargs["SecurityGroups"] = security_groups
        resp = elbv2.create_load_balancer(**kwargs)
        lb = resp["LoadBalancers"][0]
        waiter = elbv2.get_waiter("load_balancer_available")
        waiter.wait(LoadBalancerArns=[lb["LoadBalancerArn"]])
        return lb

    lb = await loop.run_in_executor(None, _call)
    return {
        "action": "create_alb",
        "name": name,
        "lb_arn": lb["LoadBalancerArn"],
        "dns_name": lb.get("DNSName", ""),
        "scheme": lb.get("Scheme", scheme),
        "lb_type": lb.get("Type", lb_type),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": name,
            "asset_type": "load_balancer",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "lb_arn": lb["LoadBalancerArn"],
                "lb_type": lb.get("Type", lb_type),
                "dns_name": lb.get("DNSName", ""),
                "scheme": lb.get("Scheme", scheme),
                "provider": "aws",
            },
            "tags": ["alb", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    lb_arn = execution_result.get("lb_arn")
    if not lb_arn:
        return {"rolled_back": False, "reason": "no lb_arn in execution result"}
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True}
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.delete_load_balancer(LoadBalancerArn=lb_arn))
    return {"rolled_back": True, "lb_arn": lb_arn}
