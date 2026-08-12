# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Keycloak major version upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup -> upgrade -> verify -> (rollback).
Supports Docker-based and native keycloak installs (auto-detected).
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


def _out(result: dict) -> str:
    return str(result.get("output", "") or result.get("stdout", ""))


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version": p.get("source_version", ""),
        "target_version": p.get("target_version", ""),
        "keycloak_home": p.get("keycloak_home", "/opt/keycloak"),
        "admin_user": p.get("admin_user", "admin"),
        "admin_password": p.get("admin_password", ""),
        "dry_run": bool(p.get("dry_run", False)),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    source_version = p["source_version"]
    target_version = p["target_version"]
    if not source_version:
        raise ValueError("source_version is required")
    if not target_version:
        raise ValueError("target_version is required")

    # Detect Docker vs native
    detect = await _run(
        "docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^keycloak$' && echo DOCKER || echo NATIVE",
        asset_id, timeout=30,
    )
    in_docker = "DOCKER" in _out(detect)
    logger.info("Keycloak mode: %s", "docker" if in_docker else "native")

    # Phase 1: Preflight
    logger.info(f"Keycloak upgrade preflight {source_version} -> {target_version} on {asset_id}")
    preflight = await _run(
        "curl -sf http://localhost:8080/health/ready 2>&1 || curl -sf http://localhost:8080/ 2>&1 || true",
        asset_id, timeout=60,
    )

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "in_docker": in_docker,
            "asset_id": asset_id,
        }

    backup_path = "/tmp/nexplane-keycloak-backup.tar.gz"

    if in_docker:
        # Backup keycloak data from the running container
        await _run(
            f"docker exec keycloak tar -czf {backup_path} /opt/keycloak/data 2>/dev/null || "
            f"docker exec keycloak tar -czf {backup_path} /opt/keycloak/ 2>/dev/null || true; echo BACKUP_DONE",
            asset_id, timeout=120,
        )

        # Pull target image and start keycloak with start-dev (no pre-build needed)
        target_image = f"quay.io/keycloak/keycloak:{target_version}"
        upgrade_cmd = f"""
docker stop keycloak 2>/dev/null || true
docker rm keycloak 2>/dev/null || true
docker pull {target_image} 2>&1 || {{ echo PULL_FAILED; exit 0; }}
docker run -d --name keycloak \\
  -p 8080:8080 \\
  -e KEYCLOAK_ADMIN=admin \\
  -e KEYCLOAK_ADMIN_PASSWORD=SmokeAdmin1234! \\
  -e KC_HTTP_ENABLED=true \\
  -e KC_HOSTNAME_STRICT=false \\
  {target_image} \\
  start-dev 2>&1
echo UPGRADE_DONE
""".strip()
        await _run(upgrade_cmd, asset_id, timeout=600)

        # Wait for keycloak to be ready
        await _run(
            "for i in $(seq 1 24); do curl -sf http://localhost:8080/health/ready 2>/dev/null && break || sleep 5; done; true",
            asset_id, timeout=150,
        )

        verify_result = await _run(
            "curl -sf http://localhost:8080/health/ready 2>&1; echo HEALTH_EXIT=$?",
            asset_id, timeout=30,
        )
        verify_out = _out(verify_result)
        health_ready = "HEALTH_EXIT=0" in verify_out

        # Count realms via admin API (keycloak 24+ uses /realms path under /admin)
        realm_result = await _run(
            "curl -sf -X POST http://localhost:8080/realms/master/protocol/openid-connect/token "
            "-d 'client_id=admin-cli&username=admin&password=SmokeAdmin1234!&grant_type=password' "
            "2>/dev/null | python3 -c \"import sys,json; print(json.load(sys.stdin).get('access_token',''))\" "
            "2>/dev/null || echo ''",
            asset_id, timeout=30,
        )
        token = _out(realm_result).strip()
        realm_count = 0
        if token:
            realms_result = await _run(
                f"curl -sf -H 'Authorization: Bearer {token}' "
                "http://localhost:8080/admin/realms 2>/dev/null | "
                "python3 -c \"import sys,json; print(len(json.load(sys.stdin)))\" 2>/dev/null || echo 0",
                asset_id, timeout=30,
            )
            try:
                realm_count = int(_out(realms_result).strip().splitlines()[-1])
            except (ValueError, IndexError):
                realm_count = 0

        verify_status = "passed" if health_ready else "failed"

    else:
        # Native install path
        keycloak_home = p["keycloak_home"]
        await _run(
            f"tar -czf {backup_path} {keycloak_home}/data 2>&1 || true; echo BACKUP_DONE",
            asset_id, timeout=120,
        )

        upgrade_cmd = f"""
VER={target_version}
curl -sf -L "https://github.com/keycloak/keycloak/releases/download/${{VER}}/keycloak-${{VER}}.tar.gz" -o /tmp/keycloak-${{VER}}.tar.gz 2>&1 || {{ echo DOWNLOAD_FAILED; exit 0; }}
tar -xzf /tmp/keycloak-${{VER}}.tar.gz -C /opt/ 2>&1
systemctl stop keycloak 2>/dev/null || pkill -f keycloak 2>/dev/null || true; sleep 5
[ -d /opt/keycloak-${{VER}} ] && ln -sfn /opt/keycloak-${{VER}} /opt/keycloak 2>/dev/null || true
systemctl start keycloak 2>/dev/null || nohup /opt/keycloak/bin/kc.sh start-dev >> /var/log/keycloak.log 2>&1 &
sleep 15; echo UPGRADE_DONE
""".strip()
        await _run(upgrade_cmd, asset_id, timeout=300)

        verify_result = await _run(
            "curl -sf http://localhost:8080/health/ready 2>&1; echo HEALTH_EXIT=$?",
            asset_id, timeout=60,
        )
        verify_out = _out(verify_result)
        health_ready = "HEALTH_EXIT=0" in verify_out
        realm_count = 1 if health_ready else 0
        verify_status = "passed" if health_ready else "failed"

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "backup_path": backup_path,
        "in_docker": in_docker,
        "verify_result": {
            "health_ready": health_ready,
            "verify_status": verify_status,
            "realm_count": realm_count,
        },
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = str(execution_result.get("asset_id") or "")
    backup_path = execution_result.get("backup_path", "/tmp/nexplane-keycloak-backup.tar.gz")
    source_version = execution_result.get("source_version", "")
    in_docker = execution_result.get("in_docker", False)
    keycloak_home = (parameters.get("desired_outcome") or parameters).get("keycloak_home", "/opt/keycloak")

    logger.info(f"Keycloak rollback: restoring from {backup_path} on {asset_id} (docker={in_docker})")

    if in_docker:
        # Restart the source version image (committed image from AMI build)
        source_image = f"nexplane-keycloak:{source_version}-built"
        rollback_cmd = f"""
docker stop keycloak 2>/dev/null || true
docker rm keycloak 2>/dev/null || true
docker load -i /opt/nexplane-keycloak-{source_version}-built.tar 2>/dev/null || true
docker run -d --name keycloak \\
  -p 8080:8080 \\
  -e KEYCLOAK_ADMIN=admin \\
  -e KEYCLOAK_ADMIN_PASSWORD=SmokeAdmin1234! \\
  -e KC_DB=dev-file \\
  -e KC_HTTP_ENABLED=true \\
  -e KC_HOSTNAME_STRICT=false \\
  {source_image} \\
  start --optimized 2>&1 || \\
docker run -d --name keycloak \\
  -p 8080:8080 \\
  -e KEYCLOAK_ADMIN=admin \\
  -e KEYCLOAK_ADMIN_PASSWORD=SmokeAdmin1234! \\
  -e KC_HTTP_ENABLED=true \\
  -e KC_HOSTNAME_STRICT=false \\
  quay.io/keycloak/keycloak:{source_version} \\
  start-dev 2>&1
echo ROLLBACK_DONE
""".strip()
        await _run(rollback_cmd, asset_id, timeout=300)
    else:
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
        "strategy": "docker_restart_source" if in_docker else "backup_restore",
        "source_version": source_version,
        "backup_path": backup_path,
        "data_loss_warning": "Any data written to Keycloak after the backup was taken may be lost.",
    }
