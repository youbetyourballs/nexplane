# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_id = parameters["resource_id"]
    confirm = parameters.get("confirm_wipe")
    if not confirm:
        return {"action": "wipe_device", "error": "confirm_wipe must be true — this action is non-reversible"}
    if not creds:
        return {"action": "wipe_device", "resource_id": resource_id, "wiped": True, "warning": "MOCK — device not actually wiped"}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    await loop.run_in_executor(None, lambda: service.mobiledevices().action(customerId="my_customer", resourceId=resource_id, body={"action": "wipe"}).execute())
    return {"action": "wipe_device", "resource_id": resource_id, "wiped": True, "warning": "Device wipe initiated — irreversible"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "device wipe is irreversible"}
