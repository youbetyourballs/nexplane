# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone
from ._client import SantaSyncClient


def _parse_ts(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_rule_deploy", "deployed": False, "verified_machines": 0, "unverified_machines": []}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    rule = {
        "rule_type": parameters["rule_type"],
        "identifier_type": parameters["identifier_type"],
        "identifier": parameters["identifier"],
    }
    if parameters.get("custom_message"):
        rule["custom_message"] = parameters["custom_message"]

    verify_machines = parameters.get("verify_machines")
    timeout_secs = parameters.get("verify_timeout_seconds", 300)

    client = SantaSyncClient(creds)
    try:
        snapshot_before = await client.get_rules(machine_group)
        previous_state = "absent"
        for r in snapshot_before:
            if r["identifier"] == rule["identifier"]:
                previous_state = r["rule_type"]
                break

        push_time = time.time()
        await client.push_rules(machine_group, [rule], mode="merge")

        verified = []
        unverified = []
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            machines = await client.list_machines(machine_group)
            if verify_machines:
                machines = [m for m in machines if m["machine_id"] in verify_machines]
            if not machines:
                break
            unverified = []
            for m in machines:
                if _parse_ts(m.get("last_sync", "")) > push_time:
                    verified.append(m["machine_id"])
                else:
                    unverified.append(m["machine_id"])
            if not unverified:
                break
            await asyncio.sleep(10)
    finally:
        await client.aclose()

    return {
        "action": "santa_rule_deploy",
        "deployed": True,
        "verified_machines": len(verified),
        "unverified_machines": unverified,
        "machine_group": machine_group,
        "identifier": rule["identifier"],
        "identifier_type": rule["identifier_type"],
        "rule_type": rule["rule_type"],
        "previous_state": previous_state,
        "snapshot_before": snapshot_before,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    machine_group = execution_result.get("machine_group")
    identifier = execution_result["identifier"]
    identifier_type = execution_result["identifier_type"]
    previous_state = execution_result.get("previous_state", "absent")
    snapshot_before = execution_result.get("snapshot_before", [])

    client = SantaSyncClient(creds)
    try:
        if previous_state == "absent":
            # Rule didn't exist before — restore the entire snapshot (which excludes this rule)
            await client.push_rules(machine_group, snapshot_before, mode="replace")
        else:
            # Rule existed with a different policy — push back the original state
            await client.push_rules(machine_group, [{"rule_type": previous_state, "identifier_type": identifier_type, "identifier": identifier}], mode="merge")
    finally:
        await client.aclose()

    return {
        "rolled_back": True,
        "identifier": identifier,
        "previous_state": previous_state,
        "machine_group": machine_group,
    }
