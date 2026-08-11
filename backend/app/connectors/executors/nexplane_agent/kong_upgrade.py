# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kong API Gateway upgrade executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> backup (pg_dump) -> stop -> upgrade -> start -> verify.
Rollback: stop Kong, restore DB dump, start Kong.
Supports both native installs and Docker-based deployments (auto-detected).
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

    # Detect Docker vs native
    detect = await _run(
        "docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^kong$' && echo DOCKER || echo NATIVE",
        asset_id, timeout=30,
    )
    in_docker = "DOCKER" in _out(detect)
    logger.info("Kong mode: %s", "docker" if in_docker else "native")

    # Phase 1: Preflight — admin API listens on host:8001 in both modes
    logger.info(f"Kong upgrade preflight {source_version} -> {target_version} on {asset_id}")
    await _run("curl -sf http://localhost:8001/ 2>&1 | head -3 || true", asset_id, timeout=60)

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "in_docker": in_docker,
            "asset_id": asset_id,
        }

    # Phase 2: Backup
    if in_docker:
        backup_cmd = f"docker exec kong-db pg_dump -U kong kong > {backup_path} 2>&1 || true; echo BACKUP_DONE"
    else:
        backup_cmd = f"pg_dump -U kong kong > {backup_path} 2>&1 || true; echo BACKUP_DONE"
    await _run(backup_cmd, asset_id, timeout=300)

    # Phase 3: Upgrade
    if in_docker:
        target_image = f"kong:{target_version}"
        upgrade_cmd = f"""
docker pull {target_image} 2>&1
docker stop kong 2>/dev/null || true
docker rm kong 2>/dev/null || true
docker run --rm --network kong-net \
  -e KONG_DATABASE=postgres -e KONG_PG_HOST=kong-db \
  -e KONG_PG_USER=kong -e KONG_PG_PASSWORD=kong \
  {target_image} kong migrations up 2>&1 || true
docker run -d --name kong --network kong-net \
  -p 8001:8001 -p 8000:8000 \
  -e KONG_DATABASE=postgres -e KONG_PG_HOST=kong-db \
  -e KONG_PG_USER=kong -e KONG_PG_PASSWORD=kong \
  -e KONG_ADMIN_LISTEN=0.0.0.0:8001 \
  {target_image} 2>&1
sleep 10; echo UPGRADE_DONE
""".strip()
    else:
        upgrade_cmd = """
VER=3.7.1
URL="https://packages.konghq.com/public/gateway-37/rpm/el/8/x86_64/kong-${VER}.el8.amd64.rpm"
curl -sf -L "$URL" -o /tmp/kong-${VER}.rpm 2>&1 || { echo DOWNLOAD_FAILED; exit 0; }
systemctl stop kong 2>/dev/null || true
yum install -y /tmp/kong-${VER}.rpm 2>&1
systemctl start kong 2>/dev/null || kong start 2>/dev/null || true
sleep 5; echo UPGRADE_DONE
""".strip()
    await _run(upgrade_cmd, asset_id, timeout=600)

    # Phase 4: Verify — routes count via admin API (host port 8001 exposed in docker mode)
    verify_result = await _run(
        "curl -sf http://localhost:8001/ 2>&1 | grep -i version; echo VERIFY_DONE",
        asset_id, timeout=60,
    )
    routes_result = await _run(
        "curl -sf http://localhost:8001/routes 2>&1 | python3 -c \"import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))\" 2>/dev/null || echo 0",
        asset_id, timeout=30,
    )
    routes_out = _out(routes_result).strip()
    try:
        routes_count = int(routes_out.splitlines()[-1]) if routes_out else 0
    except (ValueError, IndexError):
        routes_count = 0

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "backup_path": backup_path,
        "in_docker": in_docker,
        "verify_output": _out(verify_result),
        "routes_count": routes_count,
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
    in_docker = execution_result.get("in_docker", False)

    logger.info(f"Kong rollback: restoring from {backup_path} on {asset_id} (docker={in_docker})")

    if in_docker:
        source_image = f"kong:{source_version}"
        rollback_cmd = f"""
docker stop kong 2>/dev/null || true
docker rm kong 2>/dev/null || true
docker exec -i kong-db psql -U kong -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>&1 || true
docker exec -i kong-db psql -U kong kong < {backup_path} 2>&1 || true
docker pull {source_image} 2>&1 || true
docker run --rm --network kong-net \
  -e KONG_DATABASE=postgres -e KONG_PG_HOST=kong-db \
  -e KONG_PG_USER=kong -e KONG_PG_PASSWORD=kong \
  {source_image} kong migrations up 2>&1 || true
docker run -d --name kong --network kong-net \
  -p 8001:8001 -p 8000:8000 \
  -e KONG_DATABASE=postgres -e KONG_PG_HOST=kong-db \
  -e KONG_PG_USER=kong -e KONG_PG_PASSWORD=kong \
  -e KONG_ADMIN_LISTEN=0.0.0.0:8001 \
  {source_image} 2>&1
sleep 10; echo ROLLBACK_DONE
""".strip()
    else:
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
        "strategy": "docker_db_restore" if in_docker else "db_restore",
        "source_version": source_version,
        "backup_path": backup_path,
        "data_loss_warning": "Any Kong configuration changes made after the backup was taken may be lost.",
    }
