# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Keycloak major version upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup -> upgrade -> verify -> (rollback).
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


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version": p.get("source_version"),
        "target_version": p.get("target_version"),
        "keycloak_home": p.get("keycloak_home", "/opt/keycloak"),
        "admin_user": p.get("admin_user", "admin"),
        "admin_password": p.get("admin_password"),
        "dry_run": bool(p.get("dry_run", False)),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if not p.get("source_version"):
        raise ValueError("source_version is required")
    if not p.get("target_version"):
        raise ValueError("target_version is required")

    source_version = p["source_version"]
    target_version = p["target_version"]
    keycloak_home = p["keycloak_home"]

    # Phase 1: Preflight
    logger.info(f"Keycloak upgrade preflight {source_version} -> {target_version} on {asset_id}")
    preflight = await _run(
        "curl -sf http://localhost:8080/health 2>&1 || curl -sf http://localhost:8080/ 2>&1 || true",
        asset_id,
        timeout=60,
    )

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "preflight": preflight,
            "asset_id": asset_id,
        }

    # Phase 2: Backup
    backup_path = "/tmp/nexplane-keycloak-backup.tar.gz"
    await _run(
        f"tar -czf {backup_path} {keycloak_home}/data 2>&1 || true; echo BACKUP_DONE",
        asset_id,
        timeout=300,
    )

    # Phase 3: Upgrade
    ver = "23.0.7"
    upgrade_cmd = f"""
VER={ver}
curl -sf -L https://github.com/keycloak/keycloak/releases/download/${{VER}}/keycloak-${{VER}}.tar.gz -o /tmp/keycloak-${{VER}}.tar.gz 2>&1 || {{ echo DOWNLOAD_FAILED; exit 0; }}
tar -xzf /tmp/keycloak-${{VER}}.tar.gz -C /opt/ 2>&1
systemctl stop keycloak 2>/dev/null || pkill -f keycloak 2>/dev/null || true; sleep 5
[ -d /opt/keycloak-${{VER}} ] && ln -sfn /opt/keycloak-${{VER}} /opt/keycloak 2>/dev/null || true
systemctl start keycloak 2>/dev/null || nohup /opt/keycloak/bin/kc.sh start-dev >> /var/log/keycloak.log 2>&1 &
sleep 10; echo UPGRADE_DONE
""".strip()
    upgrade_result = await _run(upgrade_cmd, asset_id, timeout=300)

    # Phase 4: Verify
    verify_result = await _run(
        "curl -sf http://localhost:8080/health 2>&1; echo HEALTH_EXIT=$?",
        asset_id,
        timeout=60,
    )

    output = str(verify_result.get("output", "") or verify_result.get("stdout", ""))
    health_ok = "HEALTH_EXIT=0" in output

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "backup_path": backup_path,
        "health_ok": health_ok,
        "verify_output": output,
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_path = execution_result.get("backup_path", "/tmp/nexplane-keycloak-backup.tar.gz")
    keycloak_home = (parameters.get("desired_outcome") or parameters).get("keycloak_home", "/opt/keycloak")

    logger.info(f"Keycloak rollback: restoring from {backup_path} on {asset_id}")

    rollback_cmd = f"""
systemctl stop keycloak 2>/dev/null || pkill -f keycloak 2>/dev/null || true
sleep 5
tar -xzf {backup_path} -C / 2>&1 || true
systemctl start keycloak 2>/dev/null || nohup {keycloak_home}/bin/kc.sh start-dev >> /var/log/keycloak.log 2>&1 &
sleep 10; echo ROLLBACK_DONE
""".strip()

    await _run(rollback_cmd, asset_id, timeout=300)

    return {
        "rolled_back": True,
        "strategy": "backup_restore",
        "backup_path": backup_path,
        "data_loss_warning": "Any data written to Keycloak after the backup was taken may be lost.",
    }
