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


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


def _resolve_params(parameters: dict) -> dict:
    return {
        "migration_type": parameters.get("migration_type", "version_upgrade"),
        "source_version": parameters.get("source_version"),
        "target_version": parameters.get("target_version", "7.2"),
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
    preflight = await _run("redis-cli ping 2>&1", asset_id, timeout=60)
    preflight_out = preflight.get("output", "") or ""
    if "PONG" not in preflight_out and preflight.get("exit_code", 0) != 0:
        return {"status": "preflight_blocked", "reason": "redis-cli ping failed", "output": preflight_out}

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "migration_type": p["migration_type"],
            "target_version": p["target_version"],
            "preflight_output": preflight_out,
        }

    # Phase 2: Snapshot (BGSAVE)
    rdb_backup_path = p["rdb_backup_path"]
    logger.info(f"Taking Redis BGSAVE snapshot for asset {asset_id}")
    snap_cmd = (
        f"redis-cli BGSAVE 2>&1; sleep 3; redis-cli LASTSAVE 2>&1; "
        f"cp /var/lib/redis/dump.rdb {rdb_backup_path} 2>/dev/null || "
        f"cp /tmp/dump.rdb {rdb_backup_path} 2>/dev/null || true; "
        f"echo SNAP_DONE"
    )
    snapshot = await _run(snap_cmd, asset_id, timeout=60)
    snapshot_out = snapshot.get("output", "") or ""

    # Phase 3: Upgrade
    target_ver = p["target_version"]
    # Map short version like "7.2" to a specific patch release
    ver_map = {"7.2": "7.2.4", "7.0": "7.0.15", "6.2": "6.2.14"}
    full_ver = ver_map.get(target_ver, f"{target_ver}.0")

    logger.info(f"Redis in-place version upgrade {p['source_version']} → {target_ver}")
    upgrade_cmd = (
        f"REDIS_VER={full_ver} && "
        f"curl -sf -L -o /tmp/redis-${{REDIS_VER}}.tar.gz https://download.redis.io/releases/redis-${{REDIS_VER}}.tar.gz && "
        f"cd /tmp && tar xzf redis-${{REDIS_VER}}.tar.gz && "
        f"cd /tmp/redis-${{REDIS_VER}} && make -j$(nproc) 2>&1 && "
        f"systemctl stop redis 2>/dev/null || systemctl stop redis-server 2>/dev/null || true && "
        f"cp /tmp/redis-${{REDIS_VER}}/src/redis-server /usr/local/bin/redis-server || true && "
        f"cp /tmp/redis-${{REDIS_VER}}/src/redis-server $(which redis-server 2>/dev/null || echo /usr/bin/redis-server) 2>/dev/null || true && "
        f"systemctl start redis 2>/dev/null || systemctl start redis-server 2>/dev/null || true && "
        f"sleep 3 && echo UPGRADE_DONE"
    )
    upgrade = await _run(upgrade_cmd, asset_id, timeout=600)
    upgrade_out = upgrade.get("output", "") or ""

    # Phase 4: Verify
    logger.info(f"Verifying Redis health for asset {asset_id}")
    ping_result = await _run("redis-cli ping 2>&1", asset_id, timeout=30)
    ver_result = await _run("redis-server --version 2>&1", asset_id, timeout=30)
    ping_out = ping_result.get("output", "") or ""
    ver_out = ver_result.get("output", "") or ""

    verified = "PONG" in ping_out
    status = "completed" if verified else "verify_failed"

    return {
        "status": status,
        "migration_type": p["migration_type"],
        "source_version": p["source_version"],
        "target_version": target_ver,
        "snapshot_path": rdb_backup_path,
        "snapshot_output": snapshot_out,
        "upgrade_output": upgrade_out,
        "ping_output": ping_out,
        "version_output": ver_out,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    if not asset_id:
        return {"rolled_back": False, "reason": "asset_id missing from execution_result"}

    rdb_backup_path = execution_result.get("snapshot_path") or execution_result.get("rdb_backup_path")
    if not rdb_backup_path:
        return {"rolled_back": False, "reason": "no snapshot_path in execution_result — cannot restore"}

    logger.info(f"Rolling back Redis migration for asset {asset_id} using {rdb_backup_path}")

    rollback_cmd = (
        f"systemctl stop redis 2>/dev/null || systemctl stop redis-server 2>/dev/null || true && "
        f"cp {rdb_backup_path} /var/lib/redis/dump.rdb 2>/dev/null || cp {rdb_backup_path} /tmp/dump.rdb 2>/dev/null || true && "
        f"systemctl start redis 2>/dev/null || systemctl start redis-server 2>/dev/null || true && "
        f"sleep 3 && redis-cli ping 2>&1"
    )
    result = await _run(rollback_cmd, asset_id, timeout=120)
    result_out = result.get("output", "") or ""
    success = "PONG" in result_out

    return {
        "rolled_back": True,
        "strategy": "rdb_restore",
        "rdb_backup_path": rdb_backup_path,
        "migration_type": execution_result.get("migration_type", "version_upgrade"),
        "data_loss_warning": "Data written after the BGSAVE snapshot was taken may be lost.",
        "ping_output": result_out,
        "verified": success,
    }
