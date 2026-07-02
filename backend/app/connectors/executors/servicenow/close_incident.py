# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Close (resolve) an existing ServiceNow incident."""
    creds = getattr(connector, "credentials", {})
    sys_id = parameters["sys_id"]
    close_code = parameters.get("close_code", "Solution provided")
    close_notes = parameters.get("close_notes", "Closed by Nexplane")
    if not creds:
        return {
            "action": "close_incident",
            "sys_id": sys_id,
            "state": "7",
            "close_code": close_code,
            "mock": True,
        }
    from ._client import get_client
    body = {
        "state": "7",  # 7 = Closed in ServiceNow
        "close_code": close_code,
        "close_notes": close_notes,
    }
    async with await get_client(connector) as client:
        resp = await client.patch(f"/incident/{sys_id}", json=body)
        resp.raise_for_status()
        inc = resp.json().get("result", {})
    return {
        "action": "close_incident",
        "sys_id": sys_id,
        "number": inc.get("number"),
        "state": "7",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sys_id = execution_result.get("sys_id") or parameters.get("sys_id")
    if not sys_id:
        return {"rolled_back": False, "reason": "no sys_id in execution_result"}
    if not creds:
        return {"rolled_back": True, "sys_id": sys_id, "simulated": True}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.patch(f"/incident/{sys_id}", json={"state": "2"})  # 2 = In Progress
        resp.raise_for_status()
    return {"rolled_back": True, "sys_id": sys_id, "state": "2"}
