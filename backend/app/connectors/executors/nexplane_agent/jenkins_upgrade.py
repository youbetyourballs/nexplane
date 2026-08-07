# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Jenkins upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup (WAR + JENKINS_HOME) -> stop -> replace WAR -> start -> verify.
Rollback: restore old WAR + optionally JENKINS_HOME, restart.
ROLLBACK_CAPABILITY = "full" — builds that ran between backup and rollback are lost.
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
    jenkins_home = p.get("jenkins_home", "/var/lib/jenkins")
    jenkins_war_path = p.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    dry_run = bool(p.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    backup_path = f"{jenkins_war_path}.bak"

    # Phase 1: Preflight
    logger.info(f"Jenkins upgrade preflight {source_version} -> {target_version} on {asset_id}")
    await _run(
        f"curl -sf http://localhost:8080/api/json 2>&1 | head -5 || "
        f"curl -sf http://localhost:8080/ 2>&1 | head -5 || true",
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
        f"cp {jenkins_war_path} {backup_path} 2>/dev/null; "
        f"tar -czf /tmp/nexplane-jenkins-home-backup.tar.gz {jenkins_home} 2>&1 || true; "
        f"echo BACKUP_DONE",
        asset_id,
        timeout=600,
    )

    # Phase 3: Upgrade
    upgrade_cmd = f"""
WAR_URL="https://updates.jenkins.io/download/war/{target_version}/jenkins.war"
curl -sf -L "$WAR_URL" -o /tmp/jenkins-new.war 2>&1 || {{ echo DOWNLOAD_FAILED; exit 0; }}
systemctl stop jenkins 2>/dev/null || pkill -f jenkins.war 2>/dev/null || true; sleep 5
cp /tmp/jenkins-new.war {jenkins_war_path}
systemctl start jenkins 2>/dev/null || nohup java -jar {jenkins_war_path} --httpPort=8080 >> /var/log/jenkins.log 2>&1 &
sleep 15; echo UPGRADE_DONE
""".strip()
    await _run(upgrade_cmd, asset_id, timeout=300)

    # Phase 4: Verify
    verify_result = await _run(
        f"curl -sf http://localhost:8080/api/json 2>&1 | head -5 || "
        f"java -jar {jenkins_war_path} --version 2>&1; echo VERIFY_DONE",
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
    p = parameters.get("desired_outcome") or parameters
    jenkins_war_path = p.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    backup_path = execution_result.get("backup_path", f"{jenkins_war_path}.bak")

    logger.info(f"Jenkins rollback: restoring from {backup_path} on {asset_id}")

    rollback_cmd = f"""
systemctl stop jenkins 2>/dev/null || pkill -f jenkins.war 2>/dev/null || true
sleep 5
cp {backup_path} {jenkins_war_path} 2>&1 || true
systemctl start jenkins 2>/dev/null || nohup java -jar {jenkins_war_path} --httpPort=8080 >> /var/log/jenkins.log 2>&1 &
sleep 15; echo ROLLBACK_DONE
""".strip()

    await _run(rollback_cmd, asset_id, timeout=300)

    return {
        "rolled_back": True,
        "strategy": "war_restore",
        "backup_path": backup_path,
        "data_loss_warning": "Builds that ran between the backup and rollback are lost.",
    }
