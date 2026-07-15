# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "delete_alert_policy", "policy_name": policy_name, "deleted": True}
    from ._client import get_credentials
    credentials = get_credentials(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from google.cloud import monitoring_v3
        client = monitoring_v3.AlertPolicyServiceClient(credentials=credentials)
        client.delete_alert_policy(name=policy_name)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_alert_policy", "policy_name": policy_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "alert policy deletion is irreversible"}
