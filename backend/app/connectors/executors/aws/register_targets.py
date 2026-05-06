import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    tg_arn = parameters.get("tg_arn", "")
    targets = parameters.get("targets", [])  # list of {"Id": "i-xxx", "Port": 80}

    if not creds:
        return {"action": "register_targets", "tg_arn": tg_arn, "targets": targets, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.register_targets(TargetGroupArn=tg_arn, Targets=targets))
    return {"action": "register_targets", "tg_arn": tg_arn, "targets": targets, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    tg_arn = execution_result.get("tg_arn")
    targets = execution_result.get("targets", [])
    if not tg_arn or not targets or not creds:
        return {"rolled_back": False, "reason": "insufficient data for rollback"}
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=targets))
    return {"rolled_back": True, "tg_arn": tg_arn}
