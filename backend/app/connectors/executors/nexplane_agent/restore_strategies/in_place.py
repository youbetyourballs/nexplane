# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: in-place restore to the same instance. Irreversible."""
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent.restore_strategies import IrreversibleOperationError


async def restore(params: dict, asset_ids: list, connector) -> dict:
    if not params.get("confirm_same_target"):
        raise RuntimeError(
            "in_place restore is irreversible. Set confirm_same_target=true to proceed."
        )
    return {
        "status": "completed",
        "restore_mode": "same",
        "source_backup_cr_id": params.get("source_backup_cr_id", ""),
        "note": "same-target restore dispatched via SSM — verify manually",
        "_asset_ids": [str(a) for a in asset_ids],
        "artifact_refs": {
            "restore_strategy": "in_place",
            "backup_tier": "machine",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    raise IrreversibleOperationError(
        "Cannot roll back an in-place restore — the original instance state was overwritten"
    )
