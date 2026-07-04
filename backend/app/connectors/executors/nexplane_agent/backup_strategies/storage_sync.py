# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: S3 -> S3 server-side copy. Data tier."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_NAME = "storage_sync"


def _s3(config: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=config.get("region", config.get("aws_region", "us-east-1")),
        aws_access_key_id=config.get("aws_access_key_id"),
        aws_secret_access_key=config.get("aws_secret_access_key"),
        aws_session_token=config.get("aws_session_token"),
    )


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

    source_config = params.get("_source_config") or await _load_storage_config(
        params["source_storage_id"]
    )
    dest_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )

    if source_config.get("storage_type") != "s3" or dest_config.get("storage_type") != "s3":
        raise NotImplementedError(
            "storage_sync v1 supports S3 -> S3 only; got "
            f"{source_config.get('storage_type')} -> {dest_config.get('storage_type')}"
        )

    src_cfg = source_config["config"]
    dst_cfg = dest_config["config"]
    source_bucket = src_cfg["bucket"]
    dest_bucket = dst_cfg["bucket"]
    source_prefix = params.get("source_prefix", "")
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    captured_at = datetime.now(timezone.utc).isoformat()
    ts_safe = captured_at.replace(":", "-")
    dest_prefix = f"{dst_cfg.get('prefix', 'backups/')}{asset_id}/{ts_safe}/"

    def _sync_copy():
        src_client = _s3(src_cfg)
        dst_client = _s3(dst_cfg)
        synced_count = 0
        synced_bytes = 0
        paginator = src_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=source_bucket, Prefix=source_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(source_prefix):] if key.startswith(source_prefix) else key
                dest_key = f"{dest_prefix}{rel}"
                dst_client.copy_object(
                    Bucket=dest_bucket,
                    Key=dest_key,
                    CopySource={"Bucket": source_bucket, "Key": key},
                )
                synced_count += 1
                synced_bytes += int(obj.get("Size", 0))
        return synced_count, synced_bytes

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        synced_count, synced_bytes = await loop.run_in_executor(pool, _sync_copy)

    artifact_refs = {
        "capture_strategy": "storage_sync",
        "restore_strategy": "storage_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "source_storage_type": "s3",
        "source_bucket": source_bucket,
        "source_prefix": source_prefix,
        "dest_storage_type": "s3",
        "dest_bucket": dest_bucket,
        "dest_prefix": dest_prefix,
        "dest_config": dst_cfg,
        "synced_count": synced_count,
        "synced_bytes": synced_bytes,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    refs = execution_result.get("artifact_refs", {})
    dest_storage_type = refs.get("dest_storage_type", "s3")
    dest_prefix = refs.get("dest_prefix", "")
    dest_cfg = refs.get("dest_config") or {"bucket": refs.get("dest_bucket", "")}

    if not dest_prefix or not dest_cfg.get("bucket"):
        return {"rolled_back": False, "reason": "no dest_prefix/bucket in artifact_refs"}

    backend = get_backend(dest_storage_type)
    result = await backend.delete_prefix(dest_prefix, dest_cfg)
    return {"rolled_back": True, "deleted_prefix": dest_prefix, **result}
