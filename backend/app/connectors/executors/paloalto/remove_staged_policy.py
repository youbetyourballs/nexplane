# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def _get_firewall(creds: dict):
    from ._client import get_firewall
    return get_firewall(creds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("hostname"):
        return {"status": "error", "error": "PaloAlto connector missing hostname credential"}

    policy_name = parameters.get("policy_name") or parameters.get("staged_policy_id", "")
    if not policy_name:
        return {"status": "error", "error": "policy_name required"}

    commit = bool(parameters.get("commit", True))
    loop = asyncio.get_event_loop()

    def _remove():
        fw = _get_firewall(creds)
        fw.op(
            f"delete config xpath /config/devices/entry[@name='localhost.localdomain']"
            f"/vsys/entry[@name='vsys1']/rulebase/security/rules/entry[@name='{policy_name}']"
        )
        if commit:
            fw.op("commit")
        return True

    try:
        await loop.run_in_executor(None, _remove)
        return {
            "action": "remove_staged_policy",
            "policy_name": policy_name,
            "removed": True,
            "committed": commit,
            "removed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "action": "remove_staged_policy", "removed": False}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Staged policy removal is permanent after commit. Re-stage the policy to restore.",
        "data_loss_warning": "If the policy was committed, it must be manually re-created in candidate config.",
    }
