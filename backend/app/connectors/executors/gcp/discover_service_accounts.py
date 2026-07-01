# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_service_accounts", "service_accounts": [
            {"email": "mock-sa@project.iam.gserviceaccount.com", "disabled": False, "display_name": "Mock SA"}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import iam_admin_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = iam_admin_v1.IAMClient(credentials=credentials)
    sa_list = await loop.run_in_executor(None, lambda: list(client.list_service_accounts(name=f"projects/{project}")))
    accounts = [{"email": sa.email, "display_name": sa.display_name, "disabled": sa.disabled} for sa in sa_list]
    return {"action": "discover_service_accounts", "service_accounts": accounts, "count": len(accounts)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
