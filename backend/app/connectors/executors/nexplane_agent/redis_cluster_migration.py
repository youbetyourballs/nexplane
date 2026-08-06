# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Redis cluster migration executor.
Supports: standalone→cluster promotion, and in-place version upgrade.
Flow: preflight → snapshot (BGSAVE) → migrate/upgrade → verify → rollback.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


def _resolve_params(parameters: dict) -> dict:
    return {
        "migration_type": parameters.get("migration_type", "version_upgrade"),
        "source_version": parameters.get("source_version"),
        "target_version": parameters["target_version"],
        "cluster_nodes": parameters.get("cluster_nodes", []),
        "rdb_backup_path": parameters.get("rdb_backup_path", "/tmp/nexplane-redis-backup.rdb"),
        "dry_run": bool(parameters.get("dry_run", False)),
        "redis_host": parameters.get("redis_host", "localhost"),
        "redis_port": int(parameters.get("redis_port", 6379)),
        "redis_password": parameters.get("redis_password"),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if p["migration_type"] not in ("standalone_to_cluster", "version_upgrade"):
        raise ValueError(
            f"migration_type must be 'standalone_to_cluster' or 'version_upgrade' — got {p['migration_type']!r}"
        )
    if not p["target_version"]:
        raise ValueError("target_version required")

    # Phase 1: Preflight
    logger.info(f"Redis migration preflight for asset {asset_id}, type={p['migration_type']}, target={p['target_version']}")
    preflight = await dispatch_agent_job(
        command="preflight_redis_upgrade",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "migration_type": p["migration_type"],
            "target_version": p["target_version"],
            "preflight": preflight,
        }

    # Phase 2: Snapshot (BGSAVE)
    logger.info(f"Taking Redis BGSAVE snapshot for asset {asset_id}")
    snapshot = await dispatch_agent_job(
        command="redis_bgsave_snapshot",
        parameters={"rdb_backup_path": p["rdb_backup_path"], **p},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Phase 3: Upgrade or Migrate
    if p["migration_type"] == "version_upgrade":
        logger.info(f"Redis in-place version upgrade {p['source_version']} → {p['target_version']}")
        upgrade = await dispatch_agent_job(
            command="redis_inplace_upgrade",
            parameters=p,
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
    else:
        logger.info(f"Redis standalone → cluster migration, target_version={p['target_version']}")
        upgrade = await dispatch_agent_job(
            command="redis_cluster_init",
            parameters={"cluster_nodes": p["cluster_nodes"], **p},
            asset_ids=[asset_id],
            timeout_seconds=900,
        )

    # Phase 4: Verify
    logger.info(f"Verifying Redis cluster health for asset {asset_id}")
    verify = await dispatch_agent_job(
        command="verify_redis_cluster",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("cluster_state") in ("ok", "standalone_ok") else "verify_failed",
        "migration_type": p["migration_type"],
        "source_version": p["source_version"],
        "target_version": p["target_version"],
        "cluster_nodes_verified": verify.get("cluster_nodes_verified", []),
        "slot_coverage": verify.get("slot_coverage"),
        "rdb_backup_path": snapshot.get("rdb_backup_path", p["rdb_backup_path"]),
        "snapshot_result": snapshot,
        "upgrade_result": upgrade,
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    if not asset_id:
        return {"rolled_back": False, "reason": "asset_id missing from execution_result"}

    rdb_backup_path = execution_result.get("rdb_backup_path")
    if not rdb_backup_path:
        return {"rolled_back": False, "reason": "no rdb_backup_path in execution_result — cannot restore"}

    migration_type = execution_result.get("migration_type", "version_upgrade")
    p = _resolve_params(parameters.get("desired_outcome") or parameters)

    logger.info(f"Rolling back Redis migration (type={migration_type}) for asset {asset_id}")

    if migration_type == "version_upgrade":
        result = await dispatch_agent_job(
            command="redis_rollback_version",
            parameters={"rdb_backup_path": rdb_backup_path, "source_version": p.get("source_version"), **p},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
    else:
        result = await dispatch_agent_job(
            command="redis_rollback_cluster",
            parameters={"rdb_backup_path": rdb_backup_path, **p},
            asset_ids=[asset_id],
            timeout_seconds=900,
        )

    return {
        "rolled_back": result.get("success", False),
        "strategy": "rdb_restore",
        "rdb_backup_path": rdb_backup_path,
        "migration_type": migration_type,
        "agent_result": result,
    }
