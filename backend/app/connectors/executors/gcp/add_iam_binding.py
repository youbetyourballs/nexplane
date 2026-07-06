# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    role = parameters["role"]
    member = parameters["member"]
    if not creds:
        return {"action": "add_iam_binding", "role": role, "member": member}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _add():
        from googleapiclient.discovery import build
        crm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = crm.projects().getIamPolicy(resource=project, body={}).execute()
        bindings = policy.get("bindings", [])
        for b in bindings:
            if b["role"] == role:
                if member not in b["members"]:
                    b["members"].append(member)
                break
        else:
            bindings.append({"role": role, "members": [member]})
        policy["bindings"] = bindings
        crm.projects().setIamPolicy(
            resource=project, body={"policy": policy}
        ).execute()

    await loop.run_in_executor(None, _add)
    return {"action": "add_iam_binding", "role": role, "member": member}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.remove_iam_binding import execute as remove
    return await remove(
        {"role": execution_result.get("role", parameters["role"]),
         "member": execution_result.get("member", parameters["member"])},
        [], connector,
    )
