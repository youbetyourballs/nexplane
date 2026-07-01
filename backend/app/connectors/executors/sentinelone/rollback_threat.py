# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    threat_id = parameters["threat_id"]
    if not creds:
        return {"action": "rollback_threat", "threat_id": threat_id, "rolled_back": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/threats/actions/rollback-remediation", json={"filter": {"ids": [threat_id]}})
        resp.raise_for_status()
    return {"action": "rollback_threat", "threat_id": threat_id, "rolled_back": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "threat rollback cannot be undone"}
