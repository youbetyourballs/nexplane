# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: copy objects from source storage to target storage. Data tier."""
import logging
import os
import tempfile
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent.restore_strategies import _load_source_artifact_refs
from app.connectors.executors.nexplane_agent.storage_backends import get_backend

logger = logging.getLogger(__name__)

_NAME = "storage_restore"


async def restore(params: dict, asset_ids: list, connector) -> dict:
    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("storage_restore: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)

    source_storage_type = artifact_refs.get("dest_storage_type", "s3")
    source_config = artifact_refs.get("dest_config", {})
    source_prefix = artifact_refs.get("dest_prefix", "")

    target_storage_type = params.get("target_storage_type") or source_storage_type
    target_bucket = params.get("target_bucket") or artifact_refs.get("dest_bucket", "")
    target_prefix = params.get("target_prefix", "")

    if not target_bucket:
        raise RuntimeError("storage_restore: target_bucket is required")

    target_config = dict(source_config)
    target_config["bucket"] = target_bucket

    source_backend = get_backend(source_storage_type)
    target_backend = get_backend(target_storage_type)

    source_uris = await source_backend.list_prefix(source_prefix, source_config)

    restored_uris = []
    for uri in source_uris:
        filename = uri.rsplit("/", 1)[-1]
        target_key = f"{target_prefix}{filename}"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await source_backend.download(uri, tmp_path, source_config)
            target_uri = await target_backend.upload(tmp_path, target_key, target_config)
            restored_uris.append(target_uri)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return {
        "status": "completed",
        "restore_strategy": "storage_restore",
        "restored_uris": restored_uris,
        "target_storage_type": target_storage_type,
        "target_bucket": target_bucket,
        "target_prefix": target_prefix,
        "target_config": target_config,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    restored_uris = execution_result.get("restored_uris", [])
    if not restored_uris:
        return {"rolled_back": False, "reason": "no restored_uris in execution_result"}

    target_storage_type = execution_result.get("target_storage_type", "s3")
    target_config = execution_result.get("target_config", {})

    backend = get_backend(target_storage_type)
    for uri in restored_uris:
        await backend.delete(uri, target_config)

    return {"rolled_back": True, "deleted_count": len(restored_uris)}
