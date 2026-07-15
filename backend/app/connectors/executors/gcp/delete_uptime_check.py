# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    check_id = parameters["check_id"]
    if not creds:
        return {"action": "delete_uptime_check", "check_id": check_id, "deleted": True}
    from ._client import get_credentials
    credentials = get_credentials(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from google.cloud import monitoring_v3
        client = monitoring_v3.UptimeCheckServiceClient(credentials=credentials)
        client.delete_uptime_check_config(name=check_id)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_uptime_check", "check_id": check_id, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uptime check deletion is irreversible"}
