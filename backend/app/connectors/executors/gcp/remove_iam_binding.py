# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    role = parameters["role"]
    member = parameters["member"]
    if not creds:
        return {"action": "remove_iam_binding", "role": role, "member": member}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _remove():
        from googleapiclient.discovery import build
        crm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = crm.projects().getIamPolicy(resource=project, body={}).execute()
        for b in policy.get("bindings", []):
            if b["role"] == role and member in b.get("members", []):
                b["members"].remove(member)
        policy["bindings"] = [b for b in policy.get("bindings", []) if b.get("members")]
        crm.projects().setIamPolicy(
            resource=project, body={"policy": policy}
        ).execute()

    await loop.run_in_executor(None, _remove)
    return {"action": "remove_iam_binding", "role": role, "member": member}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "binding removal rollback requires re-adding — use add_iam_binding CR"}
