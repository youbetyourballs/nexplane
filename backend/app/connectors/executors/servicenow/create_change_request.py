# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_change_request", "number": "CHG-MOCK-001", "sys_id": "mock-sys-id"}
    from ._client import get_client
    body = {"short_description": parameters["short_description"]}
    if parameters.get("description"):
        body["description"] = parameters["description"]
    if parameters.get("type"):
        body["type"] = parameters["type"]
    async with get_client(creds) as client:
        resp = await client.post("/change_request", json=body)
        resp.raise_for_status()
        chg = resp.json()["result"]
    return {"action": "create_change_request", "number": chg.get("number"), "sys_id": chg.get("sys_id")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel change request manually"}
