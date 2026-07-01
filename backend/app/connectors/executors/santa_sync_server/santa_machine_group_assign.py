# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_machine_group_assign", "machine_id": parameters.get("machine_id"), "previous_group": None, "new_group": parameters.get("target_group")}

    machine_id = parameters["machine_id"]
    target_group = parameters["target_group"]

    client = SantaSyncClient(creds)
    try:
        machines = await client.list_machines(None)
        previous_group = next(
            (m.get("machine_group") for m in machines if m.get("machine_id") == machine_id),
            None,
        )
        await client.assign_machine_group(machine_id, target_group)
    finally:
        await client.aclose()

    return {
        "action": "santa_machine_group_assign",
        "machine_id": machine_id,
        "previous_group": previous_group,
        "new_group": target_group,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    machine_id = execution_result["machine_id"]
    previous_group = execution_result.get("previous_group")
    if not previous_group:
        return {"rolled_back": False, "reason": "no previous group recorded"}

    client = SantaSyncClient(creds)
    try:
        await client.assign_machine_group(machine_id, previous_group)
    finally:
        await client.aclose()

    return {"rolled_back": True, "machine_id": machine_id, "restored_group": previous_group}
