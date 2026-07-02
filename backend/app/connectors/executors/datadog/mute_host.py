# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    hostname = parameters["hostname"]
    if not creds:
        return {"action": "mute_host", "hostname": hostname, "muted": True}
    from ._client import get_v1_client
    body = {}
    if parameters.get("end"):
        body["end"] = parameters["end"]
    async with await get_v1_client(connector) as client:
        resp = await client.post(f"/host/{hostname}/mute", json=body)
        resp.raise_for_status()
    return {"action": "mute_host", "hostname": hostname, "muted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.datadog.unmute_host import execute as unmute
    return await unmute(parameters, [], connector)
