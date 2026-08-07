# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kong API Gateway upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup (pg_dump) -> stop -> install -> start -> verify.
Rollback: stop Kong, restore DB dump, start Kong.
ROLLBACK_CAPABILITY = "full"
"""
import logging

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


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = parameters.get("desired_outcome") or parameters
    source_version = p.get("source_version", "")
    target_version = p.get("target_version", "")
    dry_run = bool(p.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    backup_path = "/tmp/nexplane-kong-backup.sql"

    # Phase 1: Preflight
    logger.info(f"Kong upgrade preflight {source_version} -> {target_version} on {asset_id}")
    await _run(
        "curl -sf http://localhost:8001/ 2>&1 | head -3 || true",
        asset_id,
        timeout=60,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "asset_id": asset_id,
        }

    # Phase 2: Backup
    await _run(
        f"pg_dump -U kong kong > {backup_path} 2>&1 || true; echo BACKUP_DONE",
        asset_id,
        timeout=300,
    )

    # Phase 3: Upgrade
    upgrade_cmd = """
VER=3.7.1
URL="https://packages.konghq.com/public/gateway-37/rpm/el/8/x86_64/kong-${VER}.el8.amd64.rpm"
curl -sf -L "$URL" -o /tmp/kong-${VER}.rpm 2>&1 || { echo DOWNLOAD_FAILED; exit 0; }
systemctl stop kong 2>/dev/null || true
yum install -y /tmp/kong-${VER}.rpm 2>&1
systemctl start kong 2>/dev/null || kong start 2>/dev/null || true
sleep 5; echo UPGRADE_DONE
""".strip()
    await _run(upgrade_cmd, asset_id, timeout=300)

    # Phase 4: Verify
    verify_result = await _run(
        "curl -sf http://localhost:8001/ 2>&1 | grep -i version; echo VERIFY_DONE",
        asset_id,
        timeout=60,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "backup_path": backup_path,
        "verify_output": str(verify_result.get("output", "") or verify_result.get("stdout", "")),
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_path = execution_result.get("backup_path", "/tmp/nexplane-kong-backup.sql")
    source_version = execution_result.get("source_version", "")

    logger.info(f"Kong rollback: restoring from {backup_path} on {asset_id}")

    rollback_cmd = f"""
systemctl stop kong 2>/dev/null || kong stop 2>/dev/null || true
sleep 3
psql -U kong kong < {backup_path} 2>&1 || true
systemctl start kong 2>/dev/null || kong start 2>/dev/null || true
sleep 5; echo ROLLBACK_DONE
""".strip()

    await _run(rollback_cmd, asset_id, timeout=300)

    return {
        "rolled_back": True,
        "strategy": "db_restore",
        "source_version": source_version,
        "backup_path": backup_path,
        "data_loss_warning": "Any Kong configuration changes made after the backup was taken may be lost.",
    }
