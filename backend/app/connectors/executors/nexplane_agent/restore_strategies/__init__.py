# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import importlib
from types import ModuleType


class IrreversibleOperationError(Exception):
    pass


async def _load_source_artifact_refs(source_backup_cr_id: str) -> dict:
    """Shared helper: load artifact_refs from a source backup CR by ID."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    from app.models.execution_run import ExecutionRun
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, _uuid.UUID(source_backup_cr_id))
        if not cr:
            raise RuntimeError(f"Source backup CR {source_backup_cr_id} not found")
        if cr.artifact_refs:
            return cr.artifact_refs
        result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        er = result.scalar_one_or_none()
        if not er or not er.result:
            return {}
        for step in (er.result.get("execution") or {}).get("steps", []):
            refs = (step.get("result") or {}).get("artifact_refs")
            if refs:
                return refs
        return {}


_PKG = "app.connectors.executors.nexplane_agent.restore_strategies"

_STRATEGY_NAMES = (
    "launch_ami",
    "in_place",
    "import_image",
    "file_restore_to_path",
    "database_restore",
    "storage_restore",
)

_REGISTRY: dict = {}


def get_strategy(restore_strategy: str) -> ModuleType:
    if restore_strategy not in _REGISTRY:
        if restore_strategy in _STRATEGY_NAMES:
            _REGISTRY[restore_strategy] = importlib.import_module(f"{_PKG}.{restore_strategy}")
        else:
            raise ValueError(
                f"Unknown restore strategy: '{restore_strategy}'. Known: {list(_STRATEGY_NAMES)}"
            )
    return _REGISTRY[restore_strategy]
