# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import importlib
import logging
from types import ModuleType

logger = logging.getLogger(__name__)

_PKG = "app.connectors.executors.nexplane_agent.backup_strategies"

_STRATEGY_NAMES = (
    "ebs_snapshot",
    "mgn_replication",
    "disk2vhd",
    "lvm_snapshot",
    "local_files",
    "nfs_files",
    "database_dump",
    "managed_db_snapshot",
    "storage_sync",
)

_REGISTRY: dict[str, ModuleType] = {}


def get_strategy(capture_strategy: str) -> ModuleType:
    if capture_strategy not in _REGISTRY:
        if capture_strategy in _STRATEGY_NAMES:
            _REGISTRY[capture_strategy] = importlib.import_module(f"{_PKG}.{capture_strategy}")
        else:
            raise ValueError(
                f"Unknown capture strategy: '{capture_strategy}'. Known: {list(_STRATEGY_NAMES)}"
            )
    return _REGISTRY[capture_strategy]


async def _load_storage_config(backup_storage_id: str) -> dict:
    """Load BackupStorage config from DB."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.backup_storage import BackupStorage
    async with AsyncSessionLocal() as db:
        bs = await db.get(BackupStorage, _uuid.UUID(backup_storage_id))
        if not bs:
            raise RuntimeError(f"BackupStorage {backup_storage_id} not found")
        return {"storage_type": bs.storage_type, "config": bs.config}


async def _load_backup_target(asset_id: str):
    """Return the BackupTarget for this asset, or None if none exists."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.backup_target import BackupTarget
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(BackupTarget).where(
                    BackupTarget.asset_id == _uuid.UUID(asset_id)
                ).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.warning("Could not load BackupTarget for asset %s: %s", asset_id, exc)
        return None
