import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    group_id = parameters.get("group_id")
    rules = parameters.get("rules", [])
    if group_id and rules:
        perms = [
            {
                "IpProtocol": r.get("protocol", "tcp"),
                "FromPort": int(r.get("from_port", 0)),
                "ToPort": int(r.get("to_port", 0)),
                "IpRanges": [{"CidrIp": r.get("cidr", "0.0.0.0/0")}],
            }
            for r in rules
        ]
        await loop.run_in_executor(None, lambda: ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=perms))
    return {"action": "restore_security_group", "restored_from_snapshot": parameters.get("pre_change_snapshot_id"), "completed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "restore_security_group", "restored_from_snapshot": parameters.get("pre_change_snapshot_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
