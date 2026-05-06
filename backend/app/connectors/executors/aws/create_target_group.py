import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-tg")
    protocol = parameters.get("protocol", "HTTP")
    port = parameters.get("port", 80)
    vpc_id = parameters.get("vpc_id", "")
    target_type = parameters.get("target_type", "instance")

    if not creds:
        tg_arn = f"arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/{name}/mock"
        return {"action": "create_target_group", "name": name, "tg_arn": tg_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()

    def _call():
        resp = elbv2.create_target_group(
            Name=name,
            Protocol=protocol,
            Port=port,
            VpcId=vpc_id,
            TargetType=target_type,
        )
        return resp["TargetGroups"][0]

    tg = await loop.run_in_executor(None, _call)
    return {
        "action": "create_target_group",
        "name": name,
        "tg_arn": tg["TargetGroupArn"],
        "protocol": tg.get("Protocol", protocol),
        "port": tg.get("Port", port),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    tg_arn = execution_result.get("tg_arn")
    if not tg_arn:
        return {"rolled_back": False, "reason": "no tg_arn in execution result"}
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True}
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.delete_target_group(TargetGroupArn=tg_arn))
    return {"rolled_back": True, "tg_arn": tg_arn}
