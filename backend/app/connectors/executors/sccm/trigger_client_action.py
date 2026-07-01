# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

VALID_ACTIONS = frozenset(
    ["MachinePolicyRetrieve", "SoftwareInventory", "HardwareInventory", "UpdateDeploymentReEval"]
)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Trigger a client policy action on all members of an SCCM collection.

    Parameters
    ----------
    collection_id : str
        Target collection ID.
    action : str
        One of: MachinePolicyRetrieve, SoftwareInventory,
        HardwareInventory, UpdateDeploymentReEval.
    """
    collection_id = parameters.get("collection_id", "")
    action = parameters.get("action", "MachinePolicyRetrieve")

    if not collection_id:
        raise ValueError("collection_id is required")

    if action not in VALID_ACTIONS:
        raise ValueError(
            f"action '{action}' is not valid. Choose from: {sorted(VALID_ACTIONS)}"
        )

    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "sccm_trigger_client_action",
            "collection_id": collection_id,
            "client_action": action,
            "operation_id": "mock-op-id",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_sccm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_sccm_client(connector)
        return client.trigger_collection_action(collection_id, action)

    result = await loop.run_in_executor(None, _run)

    return {
        "action": "sccm_trigger_client_action",
        "collection_id": collection_id,
        "client_action": action,
        "operation_id": result.get("OperationID") or result.get("operationId", ""),
        "raw_response": result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Client action triggers are fire-and-forget — no rollback applicable."""
    return {
        "rolled_back": False,
        "reason": "trigger_client_action is a one-way signal, no rollback applicable",
    }
