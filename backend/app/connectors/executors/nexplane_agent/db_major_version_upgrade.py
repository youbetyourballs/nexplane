# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Database major version upgrade executor.

Supports PostgreSQL (pg_dump/restore default), MySQL (in-place upgrade with
auth plugin check), and MongoDB (FCV bump then binary upgrade).

Flow: preflight → dump → stop old → install new → restore/start → verify.
Rollback: restore from dump artifact or restore EBS snapshot if taken.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    db_engine = parameters.get("db_engine", "postgresql")  # postgresql | mysql | mongodb
    target_version = parameters.get("target_version", "")
    db_name = parameters.get("db_name", "")
    db_port = int(parameters.get("db_port", _default_port(db_engine)))
    dry_run = bool(parameters.get("dry_run", False))
    skip_snapshot = bool(parameters.get("skip_snapshot", False))

    if not target_version:
        raise ValueError("target_version required (e.g. '16' for PostgreSQL 16)")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight
    logger.info(f"DB upgrade preflight for asset {asset_id}, engine={db_engine}, target={target_version}")
    preflight = await dispatch_agent_job(
        command="preflight_db_upgrade",
        parameters={
            "db_engine": db_engine,
            "target_version": target_version,
            "db_port": db_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if preflight.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": preflight.get("reason", "Preflight checks failed"),
            "preflight": preflight,
        }

    if dry_run:
        return {
            "status": "dry_run",
            "db_engine": db_engine,
            "current_version": preflight.get("current_version"),
            "target_version": target_version,
            "estimated_dump_size_gb": preflight.get("estimated_dump_size_gb"),
            "warnings": preflight.get("warnings", []),
        }

    # Step 2: Optional EBS snapshot (for fast rollback on cloud hosts)
    snapshot_meta = None
    if not skip_snapshot:
        snapshot_meta = await _maybe_take_snapshot(asset_id, connector)

    # Step 3: Dump the database
    logger.info(f"Dumping database on {asset_id} before upgrade")
    dump_result = await dispatch_agent_job(
        command="dump_database",
        parameters={
            "db_engine": db_engine,
            "db_name": db_name,
            "db_port": db_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=3600,
    )
    dump_path = dump_result.get("dump_path", "")
    if not dump_path:
        raise RuntimeError(f"Database dump failed: {dump_result}")

    # Step 4: Execute version upgrade
    logger.info(f"Upgrading {db_engine} to {target_version} on {asset_id}")
    try:
        upgrade_result = await dispatch_agent_job(
            command="execute_db_upgrade",
            parameters={
                "db_engine": db_engine,
                "target_version": target_version,
                "db_name": db_name,
                "db_port": db_port,
                "dump_path": dump_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=7200,
        )
    except Exception as exc:
        logger.error(f"DB upgrade failed on {asset_id}: {exc}")
        return {
            "status": "failed",
            "error": str(exc),
            "dump_path": dump_path,
            "snapshot_meta": snapshot_meta,
            "rollback_hint": "Run rollback to restore from dump or snapshot",
        }

    # Step 5: Verify
    logger.info(f"Verifying {db_engine} upgrade on {asset_id}")
    try:
        verify_result = await dispatch_agent_job(
            command="verify_db_upgrade",
            parameters={
                "db_engine": db_engine,
                "target_version": target_version,
                "db_name": db_name,
                "db_port": db_port,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    except Exception as exc:
        verify_result = {"verified": False, "error": str(exc)}

    if not verify_result.get("verified", True):
        logger.warning(f"DB verification failed on {asset_id}, triggering rollback")
        rollback_result = await _restore_from_dump(asset_id, db_engine, db_name, db_port, dump_path)
        return {
            "status": "failed_and_rolled_back",
            "verify_result": verify_result,
            "dump_path": dump_path,
            "rollback_result": rollback_result,
        }

    return {
        "status": "completed",
        "db_engine": db_engine,
        "previous_version": preflight.get("current_version"),
        "new_version": verify_result.get("actual_version", target_version),
        "dump_path": dump_path,
        "snapshot_meta": snapshot_meta,
        "upgrade_result": upgrade_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    dump_path = execution_result.get("dump_path", "")
    db_engine = execution_result.get("db_engine", parameters.get("db_engine", "postgresql"))
    db_name = parameters.get("db_name", "")
    db_port = int(parameters.get("db_port", _default_port(db_engine)))
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""

    if not dump_path or not asset_id:
        return {
            "rolled_back": False,
            "reason": "no_dump_path_or_asset_id — manual restore required",
        }

    result = await _restore_from_dump(asset_id, db_engine, db_name, db_port, dump_path)
    return {
        "rolled_back": result.get("restored", False),
        "dump_path": dump_path,
        "restore_result": result,
    }


async def _restore_from_dump(
    asset_id: str, db_engine: str, db_name: str, db_port: int, dump_path: str
) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    try:
        return await dispatch_agent_job(
            command="restore_database",
            parameters={
                "db_engine": db_engine,
                "db_name": db_name,
                "db_port": db_port,
                "dump_path": dump_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=3600,
        )
    except Exception as exc:
        return {"restored": False, "error": str(exc)}


async def _maybe_take_snapshot(asset_id: str, connector) -> dict | None:
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, uuid.UUID(asset_id))
            if not asset:
                return None
            meta = asset.asset_metadata or {}
            instance_id = meta.get("instance_id")
            if not instance_id:
                return None
    except Exception:
        return None

    try:
        from app.connectors.executors.nexplane_agent.os_upgrade import (
            _get_aws_creds,
            _take_snapshot,
        )
        creds = await _get_aws_creds(connector)
        return await _take_snapshot(asset_id, instance_id, connector)
    except Exception as exc:
        logger.warning(f"Pre-upgrade snapshot failed (non-fatal): {exc}")
        return None


def _default_port(db_engine: str) -> int:
    return {"postgresql": 5432, "mysql": 3306, "mongodb": 27017}.get(db_engine, 5432)
