# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_machine_list", "machines": [], "machine_count": 0}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    client = SantaSyncClient(creds)
    try:
        machines = await client.list_machines(machine_group)
    finally:
        await client.aclose()

    return {
        "action": "santa_machine_list",
        "machines": machines,
        "machine_count": len(machines),
        "machine_group": machine_group,
    }
