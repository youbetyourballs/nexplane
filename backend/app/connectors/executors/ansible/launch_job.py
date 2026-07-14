# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Ansible job has already executed — output cannot be reversed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    template_id = parameters["template_id"]
    if not creds:
        return {"action": "launch_job", "template_id": template_id, "job_id": "mock-job-1", "status": "pending"}
    from ._client import get_client
    body = {}
    if parameters.get("extra_vars"):
        body["extra_vars"] = parameters["extra_vars"]
    async with get_client(creds) as client:
        resp = await client.post(f"/job_templates/{template_id}/launch/", json=body)
        resp.raise_for_status()
        job = resp.json()
    return {"action": "launch_job", "template_id": template_id, "job_id": job["id"], "status": job.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "job execution cannot be automatically reversed"}
