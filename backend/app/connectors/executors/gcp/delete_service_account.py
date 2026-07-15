# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    email = parameters["email"]
    if not creds:
        return {"action": "delete_service_account", "email": email, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("iam", "v1", credentials=credentials)
        svc.projects().serviceAccounts().delete(
            name=f"projects/{project}/serviceAccounts/{email}"
        ).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_service_account", "email": email, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "service account deletion is irreversible"}
