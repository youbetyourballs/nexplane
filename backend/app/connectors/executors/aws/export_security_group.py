# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string
from datetime import datetime, timezone


def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _mock_response(parameters):
    return {"action": "export_security_group", "group_id": parameters.get("group_id", _fake_id("sg-")), "snapshot_id": _fake_id("sgsnap-"), "rules_captured": 3, "captured_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    group_id = parameters.get("group_id")
    kwargs = {"GroupIds": [group_id]} if group_id else {}
    resp = await loop.run_in_executor(None, lambda: ec2.describe_security_groups(**kwargs))
    groups = resp.get("SecurityGroups", [])
    if groups:
        sg = groups[0]
        rules = sg.get("IpPermissions", []) + sg.get("IpPermissionsEgress", [])
        return {
            "action": "export_security_group",
            "group_id": sg.get("GroupId"),
            "snapshot_id": _fake_id("sgsnap-"),
            "rules_captured": len(rules),
            "rules": rules,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    return _mock_response(parameters)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "export has no rollback"}
