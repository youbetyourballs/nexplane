# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Jenkins upgrade executor.

Flow: preflight → backup (WAR + JENKINS_HOME) → quiet mode → wait idle →
      stop Jenkins → replace WAR → start Jenkins → plugin compat check →
      verify → cancel quiet mode.

ROLLBACK_CAPABILITY = "full" — restore old WAR + JENKINS_HOME backup.
Note: builds that ran between backup and rollback are lost.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_WAR_DOWNLOAD_BASE = "https://updates.jenkins.io/download/war"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    jenkins_home = parameters.get("jenkins_home", "/var/lib/jenkins")
    jenkins_war_path = parameters.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    jenkins_admin_url = parameters.get("jenkins_admin_url", "http://localhost:8080")
    jenkins_admin_user = parameters.get("jenkins_admin_user", "")
    jenkins_admin_password = parameters.get("jenkins_admin_password", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    war_url = f"{_WAR_DOWNLOAD_BASE}/{target_version}/jenkins.war"
    backup_war_path = f"/tmp/nexplane-jenkins-old-{asset_id[:8]}.war"
    backup_home_path = f"/tmp/nexplane-jenkins-backup-{asset_id[:8]}"

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — record version + plugin list + executor counts
    logger.info(f"Jenkins upgrade preflight {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="jenkins_preflight",
        parameters={
            "source_version": source_version,
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    busy_executors = preflight.get("busy_executors", 0)
    if busy_executors > 0:
        logger.warning(f"Jenkins has {busy_executors} busy executors — will enter quiet mode")

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "war_url": war_url,
            "busy_executors": busy_executors,
            "preflight": preflight,
        }

    # Step 2: Backup — copy WAR + JENKINS_HOME
    await dispatch_agent_job(
        command="jenkins_backup",
        parameters={
            "jenkins_home": jenkins_home,
            "jenkins_war_path": jenkins_war_path,
            "backup_war_path": backup_war_path,
            "backup_home_path": backup_home_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )

    # Step 3: Enter quiet mode
    await dispatch_agent_job(
        command="jenkins_quiet_down",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 4: Wait for executors idle (up to 10 min)
    await dispatch_agent_job(
        command="jenkins_wait_idle",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
            "timeout_seconds": 600,
        },
        asset_ids=[asset_id],
        timeout_seconds=660,
    )

    # Step 5: Stop Jenkins
    await dispatch_agent_job(
        command="jenkins_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 6: Replace WAR
    await dispatch_agent_job(
        command="jenkins_install_war",
        parameters={
            "war_url": war_url,
            "jenkins_war_path": jenkins_war_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 7: Start Jenkins
    await dispatch_agent_job(
        command="jenkins_start",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "startup_timeout_seconds": 300,
        },
        asset_ids=[asset_id],
        timeout_seconds=360,
    )

    # Step 8: Plugin compatibility check (warn, don't fail)
    compat = await dispatch_agent_job(
        command="jenkins_check_plugins",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    plugin_warnings = compat.get("warnings", [])

    # Step 9: Verify version
    verify = await dispatch_agent_job(
        command="jenkins_verify",
        parameters={
            "target_version": target_version,
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Step 10: Cancel quiet mode
    try:
        await dispatch_agent_job(
            command="jenkins_cancel_quiet_down",
            parameters={
                "jenkins_admin_url": jenkins_admin_url,
                "jenkins_admin_user": jenkins_admin_user,
                "jenkins_admin_password": jenkins_admin_password,
            },
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
    except Exception as exc:
        logger.warning(f"Could not cancel quiet mode: {exc}")

    return {
        "status": "completed" if verify.get("version_ok") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "plugin_warnings": plugin_warnings,
        "backup_war_path": backup_war_path,
        "backup_home_path": backup_home_path,
        "verify": verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_war_path = execution_result.get("backup_war_path", "")
    backup_home_path = execution_result.get("backup_home_path", "")
    jenkins_war_path = parameters.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    jenkins_home = parameters.get("jenkins_home", "/var/lib/jenkins")
    jenkins_admin_url = parameters.get("jenkins_admin_url", "http://localhost:8080")

    if not backup_war_path:
        return {"rolled_back": False, "reason": "backup_war_path missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Stop Jenkins
    await dispatch_agent_job(
        command="jenkins_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Restore old WAR
    await dispatch_agent_job(
        command="jenkins_restore_war",
        parameters={
            "backup_war_path": backup_war_path,
            "jenkins_war_path": jenkins_war_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Restore JENKINS_HOME
    if backup_home_path:
        await dispatch_agent_job(
            command="jenkins_restore_home",
            parameters={
                "backup_home_path": backup_home_path,
                "jenkins_home": jenkins_home,
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    # Start Jenkins
    await dispatch_agent_job(
        command="jenkins_start",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "startup_timeout_seconds": 300,
        },
        asset_ids=[asset_id],
        timeout_seconds=360,
    )

    return {
        "rolled_back": True,
        "source_version": execution_result.get("source_version"),
        "backup_war_path": backup_war_path,
        "backup_home_path": backup_home_path,
        "note": "Builds that ran between backup and rollback are lost.",
    }
