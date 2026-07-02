# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    hostname = parameters["hostname"]
    if not creds:
        return {"action": "unmute_host", "hostname": hostname, "muted": False}
    from ._client import get_v1_client
    async with await get_v1_client(connector) as client:
        resp = await client.post(f"/host/{hostname}/unmute")
        resp.raise_for_status()
    return {"action": "unmute_host", "hostname": hostname, "muted": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unmute rollback would mute — use mute_host explicitly"}
