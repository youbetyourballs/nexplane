# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_push_rules", "pushed": 0, "snapshot_before": [], "machine_group": "default", "mode": "merge"}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    rules = parameters.get("rules", [])
    mode = parameters.get("mode", "merge")

    client = SantaSyncClient(creds)
    try:
        snapshot_before = await client.get_rules(machine_group)
        push_result = await client.push_rules(machine_group, rules, mode=mode)
    finally:
        await client.aclose()

    return {
        "action": "santa_push_rules",
        "pushed": push_result["pushed"],
        "machine_group": push_result.get("machine_group") or machine_group or "default",
        "mode": mode,
        "snapshot_before": snapshot_before,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    snapshot = execution_result.get("snapshot_before", [])
    machine_group = execution_result.get("machine_group")

    client = SantaSyncClient(creds)
    try:
        push_result = await client.push_rules(machine_group, snapshot, mode="replace")
    finally:
        await client.aclose()

    return {
        "rolled_back": True,
        "restored_rule_count": push_result["pushed"],
        "machine_group": machine_group,
    }
