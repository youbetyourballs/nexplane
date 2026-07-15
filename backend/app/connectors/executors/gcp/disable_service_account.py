# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sa_email = parameters["service_account_email"]
    if not creds:
        return {"action": "disable_service_account", "service_account_email": sa_email, "disabled": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import iam_admin_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = iam_admin_v1.IAMClient(credentials=credentials)
    name = f"projects/{project}/serviceAccounts/{sa_email}"
    await loop.run_in_executor(None, lambda: client.disable_service_account(request={"name": name}))
    return {"action": "disable_service_account", "service_account_email": sa_email, "disabled": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sa_email = parameters["service_account_email"]
    if not creds:
        return {"action": "enable_service_account", "service_account_email": sa_email, "disabled": False}
    from ._client import get_credentials, get_project_id
    from google.cloud import iam_admin_v1
    import asyncio as _asyncio
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = _asyncio.get_event_loop()
    client = iam_admin_v1.IAMClient(credentials=credentials)
    name = f"projects/{project}/serviceAccounts/{sa_email}"
    await loop.run_in_executor(None, lambda: client.enable_service_account(request={"name": name}))
    return {"action": "enable_service_account", "service_account_email": sa_email, "disabled": False}
