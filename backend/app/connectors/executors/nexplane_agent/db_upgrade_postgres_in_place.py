# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Self-hosted PostgreSQL major version upgrade via nexplane-agent.

Flow:
  1. preflight_postgres_upgrade — verify pg_upgrade available, disk space, target pkg
  2. EBS snapshot (cloud) or skip (bare-metal)
  3. execute_postgres_upgrade — install new PG, run pg_upgrade, start new cluster
  4. verify_postgres_upgrade — confirm version + connectivity
  5. On failure: rollback_postgres_upgrade (use rollback.sh created by pg_upgrade)

Rollback:
  - Cloud: restore EBS snapshot (takes ~5 min), then restart postgres
  - Bare-metal: run rollback.sh created by pg_upgrade during execute phase
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "best_effort"

_JOB_TIMEOUT_S = 1800   # 30 min for pg_upgrade
_VERIFY_TIMEOUT_S = 120


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _d
    return await _d(command=command, parameters=parameters,
                    asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def _get_aws_creds(connector, organization_id=None):
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _get_aws_creds as _f
    return await _f(connector, organization_id=organization_id)


async def _take_snapshot(asset_id: str, instance_id: str, connector, organization_id=None) -> dict:
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _take_snapshot as _f
    return await _f(asset_id, instance_id, connector, organization_id=organization_id)


async def _restore_snapshot(asset_id: str, snapshot_id: str, snapshot_meta: dict, connector, organization_id=None) -> dict:
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _restore_snapshot as _f
    return await _f(asset_id, snapshot_id, snapshot_meta, connector, organization_id=organization_id)


async def _resolve_instance_id(asset_id: str) -> tuple:
    try:
        from app.database import AsyncSessionLocal
        from app.models.asset import Asset
        import uuid as _uuid
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, _uuid.UUID(str(asset_id)))
            if asset:
                meta = asset.asset_metadata or {}
                org = str(asset.organization_id) if asset.organization_id else None
                return meta.get("instance_id"), org
    except Exception as e:
        logger.debug("Could not resolve instance_id: %s", e)
    return None, None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("db_upgrade_postgres_in_place requires at least one asset_id")

    asset_id = asset_ids[0]
    target_version = parameters["target_version"]

    # ------------------------------------------------------------------
    # Step 1: Preflight
    # ------------------------------------------------------------------
    logger.info("db_upgrade_postgres_in_place: preflight on asset %s", asset_id)
    preflight = await dispatch_agent_job(
        "preflight_postgres_upgrade",
        {"target_version": target_version},
        [asset_id],
        _VERIFY_TIMEOUT_S,
    )
    logger.info("Preflight result: %s", preflight)
    previous_version = preflight.get("current_version", "unknown")

    # ------------------------------------------------------------------
    # Step 2: EBS snapshot (cloud only — skip if no instance_id)
    # ------------------------------------------------------------------
    instance_id, org_id = await _resolve_instance_id(asset_id)
    snapshot_id = None
    snapshot_meta = {}

    if instance_id:
        logger.info("Taking EBS snapshot of instance %s before upgrade", instance_id)
        snap = await _take_snapshot(asset_id, instance_id, connector, organization_id=org_id)
        snapshot_id = snap.get("snapshot_id")
        snapshot_meta = snap
        logger.info("Snapshot created: %s", snapshot_id)
    else:
        logger.info("No instance_id found — skipping EBS snapshot (bare-metal or non-AWS)")

    # ------------------------------------------------------------------
    # Step 3: Execute pg_upgrade
    # ------------------------------------------------------------------
    logger.info("db_upgrade_postgres_in_place: running pg_upgrade to %s", target_version)
    execute_result = await dispatch_agent_job(
        "execute_postgres_upgrade",
        {
            "target_version": target_version,
            "old_data_dir": parameters.get("old_data_dir", ""),
            "new_data_dir": parameters.get("new_data_dir", ""),
        },
        [asset_id],
        _JOB_TIMEOUT_S,
    )
    logger.info("Execute result: %s", execute_result)

    if execute_result.get("status") != "completed":
        # Auto-rollback: restore snapshot if available
        if snapshot_id and instance_id:
            logger.warning("pg_upgrade failed — restoring EBS snapshot %s", snapshot_id)
            try:
                await _restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector, organization_id=org_id)
            except Exception as rb_err:
                logger.error("Snapshot restore also failed: %s", rb_err)
        raise RuntimeError(f"execute_postgres_upgrade failed: {execute_result}")

    # ------------------------------------------------------------------
    # Step 4: Verify
    # ------------------------------------------------------------------
    verify_result = await dispatch_agent_job(
        "verify_postgres_upgrade",
        {"expected_version": target_version},
        [asset_id],
        _VERIFY_TIMEOUT_S,
    )
    logger.info("Verify result: %s", verify_result)

    return {
        "status": "completed",
        "asset_id": asset_id,
        "previous_version": previous_version,
        "target_version": target_version,
        "new_version": execute_result.get("new_version", target_version),
        "snapshot_id": snapshot_id,
        "instance_id": instance_id,
        "snapshot_meta": snapshot_meta,
        "verify_result": verify_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    snapshot_id = execution_result.get("snapshot_id")
    instance_id = execution_result.get("instance_id")
    snapshot_meta = execution_result.get("snapshot_meta", {})
    previous_version = execution_result.get("previous_version", "")

    if not asset_id:
        return {"rolled_back": False, "reason": "No asset_id in execution_result"}

    # Strategy 1: EBS snapshot restore (cloud)
    if snapshot_id and instance_id:
        logger.info("db_upgrade_postgres_in_place rollback: restoring EBS snapshot %s", snapshot_id)
        _, org_id = await _resolve_instance_id(asset_id)
        restore_result = await _restore_snapshot(
            asset_id, snapshot_id, snapshot_meta, connector, organization_id=org_id
        )
        return {
            "rolled_back": True,
            "strategy": "ebs_snapshot_restore",
            "previous_version": previous_version,
            "restore_result": restore_result,
        }

    # Strategy 2: pg_upgrade rollback (bare-metal) — rollback.sh if link mode, else restart old cluster
    logger.info("db_upgrade_postgres_in_place rollback: running rollback_postgres_upgrade on agent")
    target_version = execution_result.get("target_version", "") or ""
    rollback_result = await dispatch_agent_job(
        "rollback_postgres_upgrade",
        {"previous_version": previous_version, "target_version": target_version},
        [asset_id],
        _JOB_TIMEOUT_S,
    )
    if rollback_result.get("rolled_back"):
        return {
            "rolled_back": True,
            "strategy": "pg_upgrade_rollback_sh",
            "previous_version": previous_version,
            "rollback_result": rollback_result,
        }

    return {
        "rolled_back": False,
        "reason": f"Both EBS restore and pg_upgrade rollback.sh failed: {rollback_result}",
    }
