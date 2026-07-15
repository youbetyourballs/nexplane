# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "SCCM script side effects on target cannot be automatically undone"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Run a pre-approved SCCM script against a collection.

    Scripts must be approved in the SCCM console before invocation.
    No freeform script content is accepted — only a script GUID.

    Parameters
    ----------
    script_guid : str
        GUID of the approved script in SCCM (e.g. "{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}").
    collection_id : str
        Target collection ID.
    """
    script_guid = parameters.get("script_guid", "")
    collection_id = parameters.get("collection_id", "")

    if not script_guid:
        raise ValueError("script_guid is required")
    if not collection_id:
        raise ValueError("collection_id is required")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "sccm_run_script",
            "script_guid": script_guid,
            "collection_id": collection_id,
            "operation_id": "mock-op-id",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_sccm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_sccm_client(connector)
        return client.run_script(script_guid, collection_id)

    result = await loop.run_in_executor(None, _run)

    return {
        "action": "sccm_run_script",
        "script_guid": script_guid,
        "collection_id": collection_id,
        "operation_id": result.get("OperationID") or result.get("operationId", ""),
        "raw_response": result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Script execution has no automated rollback — scripts must handle their own idempotency."""
    return {
        "rolled_back": False,
        "reason": (
            "SCCM script execution rollback is not automated. "
            "Scripts must be idempotent or include their own undo logic."
        ),
    }
