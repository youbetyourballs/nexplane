# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    if not creds:
        return {"action": "unsuspend_user", "user_email": user_email, "suspended": False}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    await loop.run_in_executor(None, lambda: service.users().update(userKey=user_email, body={"suspended": False}).execute())
    return {"action": "unsuspend_user", "user_email": user_email, "suspended": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unsuspend rollback would suspend — use suspend_user explicitly"}
