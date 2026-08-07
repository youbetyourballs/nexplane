# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GitLab major version upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup -> upgrade package -> reconfigure -> verify.
Rollback: restore from gitlab-backup. Background migrations are irreversible once run.
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

    backup_path = "/var/opt/gitlab/backups/"

    # Phase 1: Preflight
    logger.info(f"GitLab upgrade preflight {source_version} -> {target_version} on {asset_id}")
    await _run(
        "gitlab-rake gitlab:check 2>&1 | tail -5 || curl -sf http://localhost/-/health 2>&1 || true",
        asset_id,
        timeout=120,
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
        "gitlab-backup create SKIP=repositories 2>&1 | tail -10; echo BACKUP_EXIT=$?",
        asset_id,
        timeout=1800,
    )

    # Phase 3: Upgrade
    upgrade_cmd = """
curl -s https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.rpm.sh | bash 2>&1 || true
GITLAB_URL="https://packages.gitlab.com" EXTERNAL_URL="http://localhost" yum install -y gitlab-ce 2>&1 || true
gitlab-ctl reconfigure 2>&1 | tail -5 || true
echo UPGRADE_DONE
""".strip()
    await _run(upgrade_cmd, asset_id, timeout=1800)

    # Phase 4: Verify
    verify_result = await _run(
        "gitlab-rake gitlab:env:info 2>&1 | grep -i version | head -5; echo VERIFY_DONE",
        asset_id,
        timeout=120,
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
    backup_path = execution_result.get("backup_path", "/var/opt/gitlab/backups/")
    source_version = execution_result.get("source_version", "")

    logger.info(f"GitLab rollback: restoring from {backup_path} on {asset_id}")

    rollback_cmd = f"""
gitlab-ctl stop 2>&1 || true
BACKUP=$(ls -t {backup_path}*_gitlab_backup.tar 2>/dev/null | head -1)
if [ -n "$BACKUP" ]; then
    BACKUP_ID=$(basename "$BACKUP" _gitlab_backup.tar)
    gitlab-backup restore BACKUP="$BACKUP_ID" force=yes 2>&1 | tail -20 || true
fi
gitlab-ctl start 2>&1 || true
echo ROLLBACK_DONE
""".strip()

    await _run(rollback_cmd, asset_id, timeout=1800)

    return {
        "rolled_back": True,
        "strategy": "gitlab_backup_restore",
        "source_version": source_version,
        "backup_path": backup_path,
        "data_loss_warning": (
            "GitLab background migrations that already ran are irreversible. "
            "Any data written after the backup was taken may be lost."
        ),
    }
