# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account_id = parameters["account_id"]
    display_name = parameters.get("display_name", account_id)
    if not creds:
        project = "mock-project"
        return {
            "action": "create_service_account",
            "email": f"{account_id}@{project}.iam.gserviceaccount.com",
            "unique_id": "mock-unique-id",
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("iam", "v1", credentials=credentials)
        sa = svc.projects().serviceAccounts().create(
            name=f"projects/{project}",
            body={
                "accountId": account_id,
                "serviceAccount": {"displayName": display_name},
            },
        ).execute()
        email = sa["email"]
        # Poll for up to 30s — GCP SA creation is eventually consistent
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                svc.projects().serviceAccounts().get(
                    name=f"projects/{project}/serviceAccounts/{email}"
                ).execute()
                break
            except Exception:
                time.sleep(2)
        return email, sa["uniqueId"]

    email, unique_id = await loop.run_in_executor(None, _create)
    return {"action": "create_service_account", "email": email, "unique_id": unique_id}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_service_account import execute as delete
    email = execution_result.get("email", "")
    if not email:
        return {"rolled_back": False, "reason": "no email in execution_result"}
    return await delete({"email": email}, [], connector)
