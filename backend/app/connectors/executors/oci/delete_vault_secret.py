# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone, timedelta
from ._client import get_vault_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_id = parameters.get("secret_id", "")
    deletion_days = int(parameters.get("deletion_time_days", 1))

    if not creds:
        return {"action": "delete_vault_secret", "secret_id": secret_id, "mock": True}

    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()
    deletion_time = datetime.now(timezone.utc) + timedelta(days=deletion_days)

    def _call():
        import oci.vault.models
        details = oci.vault.models.ScheduleSecretDeletionDetails(
            time_of_deletion=deletion_time
        )
        vault_client.schedule_secret_deletion(secret_id, details)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_vault_secret",
        "secret_id": secret_id,
        "scheduled_deletion": deletion_time.isoformat(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_id = execution_result.get("secret_id", parameters.get("secret_id", ""))
    if not creds:
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "mock": True}
    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: vault_client.cancel_secret_deletion(secret_id))
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "cancelled": True}
    except Exception as e:
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "cancelled": False, "error": str(e)}
