# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    resource_id = parameters.get("resource_id")
    confirm_wipe = parameters.get("confirm_wipe", False)

    if not confirm_wipe:
        return {
            "action": "wipe_mobile_device",
            "user_email": user_email,
            "skipped": True,
            "reason": "confirm_wipe must be True to execute this irreversible action.",
        }

    if not creds:
        return {"action": "wipe_mobile_device", "user_email": user_email, "wiped": True, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        if resource_id:
            device_ids = [resource_id]
        else:
            resp = service.mobiledevices().list(
                customerId="my_customer", query=f"email:{user_email}"
            ).execute()
            device_ids = [d["resourceId"] for d in resp.get("mobiledevices", [])]

        wiped = []
        errors = []
        for rid in device_ids:
            try:
                service.mobiledevices().action(
                    customerId="my_customer",
                    resourceId=rid,
                    body={"action": "wipe"},
                ).execute()
                wiped.append(rid)
            except Exception as exc:
                errors.append({"resourceId": rid, "error": str(exc)})
        return {"devices_wiped": wiped, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "wipe_mobile_device",
        "user_email": user_email,
        "wiped": len(result["devices_wiped"]) > 0,
        "rollback_data": None,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Device wipe is irreversible. All data on the device has been deleted."}
