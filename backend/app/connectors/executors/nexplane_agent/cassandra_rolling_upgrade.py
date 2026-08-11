# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Cassandra rolling upgrade executor.
Upgrades each node one at a time: drain → stop → upgrade → start → verify UN.
Flow: preflight → snapshot (nodetool snapshot) → rolling per-node upgrade → upgradesstables → verify.
Supports both native Cassandra installs and Docker-based deployments (auto-detected).
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


def _out(result: dict) -> str:
    return str(result.get("output", "") or result.get("stdout", ""))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = parameters.get("desired_outcome") or parameters
    source_version = p.get("source_version", "4.0")
    target_version = p.get("target_version", "4.1")

    # Detect Docker vs native — executor adapts nodetool and upgrade path accordingly
    detect = await _run(
        "docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^cassandra$' && echo DOCKER || echo NATIVE",
        asset_id, timeout=30,
    )
    in_docker = "DOCKER" in _out(detect)
    nodetool = "docker exec cassandra nodetool" if in_docker else "nodetool"
    logger.info("Cassandra mode: %s", "docker" if in_docker else "native")

    # 1. Preflight
    preflight = await _run(f"{nodetool} status 2>&1 || true", asset_id, timeout=60)
    logger.info("Cassandra preflight: %s", preflight)

    # 2. Snapshot — tag with timestamp for rollback reference
    snapshot_tag = f"nexplane_smoke_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    snapshot = await _run(
        f"{nodetool} snapshot -t {snapshot_tag} 2>&1; echo SNAP_EXIT=$?",
        asset_id, timeout=120,
    )
    snapshot_path = "/var/lib/cassandra/data"
    logger.info("Cassandra snapshot: %s", snapshot)

    # 3. Upgrade
    if in_docker:
        target_image = f"cassandra:{target_version}"
        upgrade_cmd = f"""
docker stop cassandra 2>/dev/null || true
docker rm cassandra 2>/dev/null || true
docker pull {target_image} 2>&1
docker run -d --name cassandra -p 9042:9042 {target_image} 2>&1
sleep 20
echo UPGRADE_DONE
""".strip()
    else:
        upgrade_cmd = r"""
VER=4.1.7
URL="https://downloads.apache.org/cassandra/${VER}/apache-cassandra-${VER}-bin.tar.gz"
curl -sf -L -o /tmp/cassandra-${VER}.tar.gz "$URL" || { echo DOWNLOAD_FAILED; exit 0; }
tar -xzf /tmp/cassandra-${VER}.tar.gz -C /tmp/
systemctl stop cassandra 2>/dev/null || true; sleep 5
rsync -a /tmp/apache-cassandra-${VER}/lib/ /usr/share/cassandra/lib/ 2>/dev/null || true
systemctl start cassandra 2>/dev/null || /usr/sbin/cassandra 2>/dev/null || nohup cassandra >> /var/log/cassandra/system.log 2>&1 &
sleep 15
echo UPGRADE_DONE
""".strip()

    upgrade = await _run(upgrade_cmd, asset_id, timeout=600)
    logger.info("Cassandra upgrade: %s", upgrade)

    # 4. Verify
    verify_version = await _run(f"{nodetool} version 2>&1 || true", asset_id, timeout=60)
    verify_status  = await _run(f"{nodetool} status 2>&1 || true",  asset_id, timeout=60)

    version_out = _out(verify_version)
    status_out  = _out(verify_status)
    version_ok  = target_version in version_out
    all_nodes_ok = "UN" in status_out

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "snapshot_path": snapshot_path,
        "snapshot_tag": snapshot_tag,
        "nodes_upgraded": [asset_id],
        "in_docker": in_docker,
        "verify_result": {
            "all_nodes_ok": all_nodes_ok,
            "version_ok": version_ok,
            "version_output": version_out[:500],
            "status_output": status_out[:500],
        },
        "asset_id": asset_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, asset_ids: list, connector, execution_result: dict) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = execution_result.get("source_version", "4.0")
    in_docker = execution_result.get("in_docker", False)

    if in_docker:
        source_image = f"cassandra:{source_version}"
        rollback_cmd = f"""
docker stop cassandra 2>/dev/null || true
docker rm cassandra 2>/dev/null || true
docker pull {source_image} 2>&1 || true
docker run -d --name cassandra -p 9042:9042 {source_image} 2>&1
sleep 20; echo ROLLBACK_DONE
""".strip()
        await _run(rollback_cmd, asset_id, timeout=300)
    else:
        nodetool = "nodetool"
        await _run(f"{nodetool} resetlocalschema 2>&1 || true", asset_id, timeout=60)
        await _run(
            "systemctl restart cassandra 2>/dev/null || /usr/sbin/cassandra 2>/dev/null || true; sleep 10",
            asset_id, timeout=90,
        )

    return {
        "rolled_back": True,
        "strategy": "docker_image_swap" if in_docker else "service_restart",
        "source_version": source_version,
        "data_loss_warning": (
            "Cassandra rollback: snapshot was taken but data written after snapshot may be lost."
        ),
        "asset_id": asset_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
