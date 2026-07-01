# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    job_id = parameters["job_id"]
    if not creds:
        return {"action": "cancel_job", "job_id": job_id, "cancelled": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/jobs/{job_id}/cancel/")
        resp.raise_for_status()
    return {"action": "cancel_job", "job_id": job_id, "cancelled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel has no rollback"}
