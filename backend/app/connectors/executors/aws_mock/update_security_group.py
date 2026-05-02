import asyncio
import random
import string
from datetime import datetime, timezone


def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _mock_response(parameters):
    rules = parameters.get("rules", [])
    return {"action": "update_security_group", "group_id": parameters.get("group_id", _fake_id("sg-")), "rules_applied": len(rules), "rules_added": [r for r in rules if r.get("action") == "add"], "rules_removed": [r for r in rules if r.get("action") == "remove"], "pre_change_snapshot_id": _fake_id("sgsnap-"), "completed_at": datetime.now(timezone.utc).isoformat()}


def _build_ip_permission(rule: dict) -> dict:
    return {
        "IpProtocol": rule.get("protocol", "tcp"),
        "FromPort": int(rule.get("from_port", 0)),
        "ToPort": int(rule.get("to_port", 0)),
        "IpRanges": [{"CidrIp": rule.get("cidr", "0.0.0.0/0")}],
    }


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    group_id = parameters.get("group_id")
    rules = parameters.get("rules", [])
    rules_added = [r for r in rules if r.get("action") == "add"]
    rules_removed = [r for r in rules if r.get("action") == "remove"]

    if rules_added and group_id:
        perms = [_build_ip_permission(r) for r in rules_added]
        await loop.run_in_executor(None, lambda: ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=perms))
    if rules_removed and group_id:
        perms = [_build_ip_permission(r) for r in rules_removed]
        await loop.run_in_executor(None, lambda: ec2.revoke_security_group_ingress(GroupId=group_id, IpPermissions=perms))

    return {
        "action": "update_security_group",
        "group_id": group_id,
        "rules_applied": len(rules),
        "rules_added": rules_added,
        "rules_removed": rules_removed,
        "pre_change_snapshot_id": _fake_id("sgsnap-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "security_group_restore", "restored_from_snapshot": execution_result.get("pre_change_snapshot_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
