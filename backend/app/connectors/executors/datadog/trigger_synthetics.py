# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    test_id = parameters["test_public_id"]
    if not creds:
        return {"action": "trigger_synthetics", "test_id": test_id, "triggered": True}
    from ._client import get_v1_client
    async with await get_v1_client(connector) as client:
        resp = await client.post("/synthetics/tests/trigger/ci", json={"tests": [{"public_id": test_id}]})
        resp.raise_for_status()
    return {"action": "trigger_synthetics", "test_id": test_id, "triggered": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "synthetic test trigger has no rollback"}
