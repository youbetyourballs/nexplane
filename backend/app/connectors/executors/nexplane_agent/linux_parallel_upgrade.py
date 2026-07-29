# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import base64
import logging
import os
import re
import subprocess
import tempfile

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

DEFINITION = {
    "name": "linux_parallel_upgrade",
    "display_name": "Linux Parallel Upgrade",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"


async def _check_agent(asset_id: str) -> None:
    """Raise RuntimeError if agent is unreachable."""
    try:
        await dispatch_agent_job("health_check", {}, [asset_id], timeout_seconds=30)
    except Exception as exc:
        raise RuntimeError(f"Agent on asset {asset_id} unreachable: {exc}") from exc


async def _get_os_version(asset_id: str) -> tuple:
    """Return OS version as comparable tuple, e.g. (20, 4) for Ubuntu 20.04."""
    result = await dispatch_agent_job(
        "run_command",
        {"command": "grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"'", "timeout": 15},
        [asset_id],
        timeout_seconds=20,
    )
    version_str = result.get("output", "").strip()
    parts = re.findall(r'\d+', version_str)
    if not parts:
        raise RuntimeError(f"Could not parse OS version from asset {asset_id}: {version_str!r}")
    return tuple(int(p) for p in parts)


async def _preflight(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1. Returns preflight report dict. Raises RuntimeError on failure."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    sync_paths = parameters["sync_paths"]
    cutover_method = parameters["cutover_method"]
    cutover_config = parameters["cutover_config"]

    await _check_agent(source_id)
    await _check_agent(dest_id)

    source_ver = await _get_os_version(source_id)
    dest_ver = await _get_os_version(dest_id)
    if dest_ver <= source_ver:
        raise RuntimeError(
            f"Dest OS version {dest_ver} must be strictly greater than source OS version {source_ver}"
        )

    # Verify sync_paths exist on source
    for path in sync_paths:
        result = await dispatch_agent_job(
            "run_command",
            {"command": f"test -e {path} && echo ok || echo missing", "timeout": 10},
            [source_id],
            timeout_seconds=15,
        )
        if "missing" in result.get("output", ""):
            raise RuntimeError(f"sync_paths entry {path!r} does not exist on source")

    # Estimate disk usage on source
    paths_arg = " ".join(sync_paths)
    du_result = await dispatch_agent_job(
        "run_command",
        {"command": f"du -sh {paths_arg} 2>/dev/null | tail -1", "timeout": 30},
        [source_id],
        timeout_seconds=35,
    )

    # Verify cutover method preconditions
    if cutover_method == "eip":
        eip_id = cutover_config.get("eip_allocation_id")
        if not eip_id:
            raise RuntimeError("cutover_config.eip_allocation_id is required for method 'eip'")
    elif cutover_method == "alb":
        if not cutover_config.get("target_group_arn"):
            raise RuntimeError("cutover_config.target_group_arn is required for method 'alb'")
    elif cutover_method == "dns":
        for field in ("hosted_zone_id", "record_name", "record_type", "ttl"):
            if not cutover_config.get(field):
                raise RuntimeError(f"cutover_config.{field} is required for method 'dns'")
    elif cutover_method == "static_ip":
        for field in ("interface", "ip", "netmask", "gateway"):
            if not cutover_config.get(field):
                raise RuntimeError(f"cutover_config.{field} is required for method 'static_ip'")
    else:
        raise RuntimeError(f"Unknown cutover_method: {cutover_method!r}")

    return {
        "source_os_version": list(source_ver),
        "dest_os_version": list(dest_ver),
        "disk_estimate": du_result.get("output", "unknown"),
    }


async def _phase2_snapshot(source_id: str, connector, execution_result: dict) -> dict:
    """Take EBS snapshot of source (EC2) or skip with warning (on-prem).

    Uses IMDS to discover the source instance ID. If IMDS fails (non-EC2),
    treats the host as on-prem and skips snapshot with snapshot_skipped=True.
    """
    # Try to get instance ID via IMDS on the source agent
    try:
        imds_result = await dispatch_agent_job(
            "run_command",
            {"command": "curl -sf http://169.254.169.254/latest/meta-data/instance-id", "timeout": 5},
            [source_id],
            timeout_seconds=10,
        )
        instance_id = imds_result.get("output", "").strip()
        exit_code = imds_result.get("exit_code", 1)
        if not instance_id or exit_code != 0 or not instance_id.startswith("i-"):
            raise RuntimeError("IMDS returned non-instance-id output")
    except Exception as exc:
        logger.warning(f"[linux_parallel_upgrade] IMDS lookup failed ({exc}); treating as on-prem, skipping snapshot")
        return {"snapshot_id": None, "snapshot_skipped": True}

    if not connector.credentials or not connector.credentials.get("access_key_id"):
        logger.warning("[linux_parallel_upgrade] No AWS creds on connector; skipping snapshot")
        return {"snapshot_id": None, "snapshot_skipped": True}

    try:
        snap = await _take_snapshot(source_id, instance_id, connector)
        return {"snapshot_id": snap["snapshot_id"], "snapshot_meta": snap, "snapshot_skipped": False}
    except Exception as exc:
        logger.warning(f"[linux_parallel_upgrade] Snapshot failed (non-fatal): {exc}")
        return {"snapshot_id": None, "snapshot_skipped": True}


async def _generate_temp_keypair() -> tuple:
    """Generate temp Ed25519 keypair. Returns (public_key_line, private_key_b64)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = os.path.join(tmpdir, "rsync_key")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "nexplane-rsync-temp"],
            check=True,
            capture_output=True,
        )
        with open(key_path, "rb") as f:
            private_key_b64 = base64.b64encode(f.read()).decode()
        with open(f"{key_path}.pub", "r") as f:
            public_key = f.read().strip()
    return public_key, private_key_b64


async def _run_rsync(source_id: str, dest_id: str, parameters: dict) -> dict:
    """Install temp key on dest, run rsync_push from source, remove temp key from dest."""
    sync_paths = parameters["sync_paths"]
    sync_exclude = parameters.get("sync_exclude", [])

    pub_key, priv_key_b64 = await _generate_temp_keypair()

    # Get dest host IP
    ip_result = await dispatch_agent_job(
        "run_command",
        {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
        [dest_id],
        timeout_seconds=15,
    )
    dest_ip = ip_result.get("output", "").strip().split()[0]
    if not dest_ip:
        raise RuntimeError("Could not determine dest host IP for rsync")

    try:
        await dispatch_agent_job(
            "add_authorized_key",
            {"public_key": pub_key, "user": "root"},
            [dest_id],
            timeout_seconds=15,
        )
        result = await dispatch_agent_job(
            "rsync_push",
            {
                "dest_host": dest_ip,
                "dest_user": "root",
                "dest_ssh_key": priv_key_b64,
                "paths": sync_paths,
                "excludes": sync_exclude,
                "delete": True,
            },
            [source_id],
            timeout_seconds=600,
        )
    finally:
        try:
            await dispatch_agent_job(
                "remove_authorized_key",
                {"public_key": pub_key, "user": "root"},
                [dest_id],
                timeout_seconds=15,
            )
        except Exception as exc:
            logger.warning(f"Failed to remove temp authorized key from dest: {exc}")

    return {
        "bytes_transferred": result.get("bytes_transferred", 0),
        "files_transferred": result.get("files_transferred", 0),
        "duration_seconds": result.get("duration_seconds", 0),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1-6: preflight → snapshot → sync → health check → cutover → decommission scheduling."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    pre_sync_runs = parameters.get("pre_sync_runs", 1)
    dry_run = parameters.get("dry_run", False)

    execution_result = {
        "snapshot_id": None,
        "snapshot_skipped": False,
        "cutover_completed": False,
        "source_stopped": False,
        "decommission_job_id": None,
        "decommission_manual": False,
        "rollback_capability": ROLLBACK_CAPABILITY_FULL,
        "cutover_method": parameters.get("cutover_method"),
        "cutover_config": parameters.get("cutover_config"),
        "error": None,
        "preflight": None,
        "sync_runs": [],
        "dry_run": dry_run,
    }

    try:
        # Phase 1 — Preflight
        logger.info(f"[linux_parallel_upgrade] Phase 1: preflight source={source_id} dest={dest_id}")
        preflight_report = await _preflight(parameters, asset_ids, connector)
        execution_result["preflight"] = preflight_report

        if dry_run:
            logger.info("[linux_parallel_upgrade] dry_run=True — stopping after preflight")
            return execution_result

        # Phase 2 — Snapshot source
        logger.info("[linux_parallel_upgrade] Phase 2: snapshot source")
        snap_result = await _phase2_snapshot(source_id, connector, execution_result)
        execution_result.update(snap_result)

        # Phase 3 — Pre-sync runs (N-1 runs; final run happens in Phase 5)
        pre_runs = max(pre_sync_runs - 1, 0)
        for i in range(pre_runs):
            logger.info(f"[linux_parallel_upgrade] Phase 3: pre-sync run {i + 1}/{pre_runs}")
            run_stats = await _run_rsync(source_id, dest_id, parameters)
            execution_result["sync_runs"].append(run_stats)

        # Phases 4-6 not yet implemented
        raise NotImplementedError("Phases 4-6 not implemented in this task")

    except NotImplementedError:
        raise
    except Exception as exc:
        logger.exception(f"[linux_parallel_upgrade] execute failed: {exc}")
        execution_result["error"] = str(exc)
        return execution_result


async def rollback(parameters: dict, execution_result: dict, asset_ids: list, connector) -> dict:
    raise NotImplementedError("rollback not yet implemented")
