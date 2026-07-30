# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import base64
import logging
import os
import re
import shlex
import socket
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

_HEALTH_CHECK_RETRY_SLEEP = 3  # seconds between TCP probe retries; override in tests
_HEALTH_CHECK_TIMEOUT_SECONDS = 30  # total wait per port; override in tests

DEFINITION = {
    "name": "linux_parallel_upgrade",
    "display_name": "Linux Parallel Upgrade",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY = "full"
ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"


async def _check_agent(asset_id: str, timeout_seconds: int = 90) -> None:
    """Raise RuntimeError if agent is unreachable."""
    try:
        await dispatch_agent_job("health_check", {}, [asset_id], timeout_seconds=timeout_seconds)
    except Exception as exc:
        raise RuntimeError(f"Agent on asset {asset_id} unreachable: {exc}") from exc


async def _get_os_version(asset_id: str) -> tuple:
    """Return OS version as comparable tuple, e.g. (20, 4) for Ubuntu 20.04."""
    result = await dispatch_agent_job(
        "run_command",
        {"command": "grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"'", "timeout": 15},
        [asset_id],
        timeout_seconds=90,
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
            {"command": f"test -e {shlex.quote(path)} && echo ok || echo missing", "timeout": 10},
            [source_id],
            timeout_seconds=90,
        )
        if "missing" in result.get("output", ""):
            raise RuntimeError(f"sync_paths entry {path!r} does not exist on source")

    # Estimate disk usage on source
    paths_arg = " ".join(shlex.quote(p) for p in sync_paths)
    du_result = await dispatch_agent_job(
        "run_command",
        {"command": f"du -sh {paths_arg} 2>/dev/null | tail -1", "timeout": 30},
        [source_id],
        timeout_seconds=90,
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
            timeout_seconds=90,
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

    # Confirmed EC2 — take snapshot; failures here are hard errors (don't swallow)
    snap = await _take_snapshot(source_id, instance_id, connector)
    return {"snapshot_id": snap["snapshot_id"], "snapshot_meta": snap, "snapshot_skipped": False}


async def _generate_temp_keypair() -> tuple:
    """Generate temp Ed25519 keypair. Returns (public_key_line, private_key_b64)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _generate_temp_keypair_sync)


def _generate_temp_keypair_sync() -> tuple:
    """Sync helper — run in executor to avoid blocking event loop."""
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
        timeout_seconds=90,
    )
    dest_ip = ip_result.get("output", "").strip().split()[0]
    if not dest_ip:
        raise RuntimeError("Could not determine dest host IP for rsync")

    try:
        await dispatch_agent_job(
            "add_authorized_key",
            {"public_key": pub_key, "user": "root"},
            [dest_id],
            timeout_seconds=90,
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
                timeout_seconds=90,
            )
        except Exception as exc:
            logger.warning(f"Failed to remove temp authorized key from dest: {exc}")

    return {
        "bytes_transferred": result.get("bytes_transferred", 0),
        "files_transferred": result.get("files_transferred", 0),
        "duration_seconds": result.get("duration_seconds", 0),
    }


def _probe_tcp_port(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


async def _verify_dest_health(parameters: dict, dest_ip: str) -> None:
    """Phase 4. Raises RuntimeError on any health check failure."""
    dest_id = parameters["dest_asset_id"]
    ports = parameters.get("health_check_ports") or []
    health_cmd = parameters.get("health_check_command")

    for port in ports:
        ok = False
        deadline = asyncio.get_running_loop().time() + _HEALTH_CHECK_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if _probe_tcp_port(dest_ip, port):
                ok = True
                break
            await asyncio.sleep(_HEALTH_CHECK_RETRY_SLEEP)
        if not ok:
            raise RuntimeError(f"Health check failed: port {port} unreachable on dest after 30s")

    if health_cmd:
        result = await dispatch_agent_job(
            "run_command",
            {"command": health_cmd, "timeout": 60},
            [dest_id],
            timeout_seconds=70,
        )
        if result.get("exit_code", 1) != 0:
            raise RuntimeError(
                f"Health check command exited {result.get('exit_code')}: {result.get('output', '')}"
            )

    await _check_agent(dest_id)


async def _instance_id_for_asset(asset_id: str, ec2_client=None) -> str:
    """Return EC2 instance ID for an asset.

    Tries asset_metadata first (works for assets registered without EC2 tags),
    then falls back to describe_instances tag lookup.
    """
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, uuid.UUID(asset_id))
        instance_id = (asset.asset_metadata or {}).get("instance_id") if asset else None

    if instance_id:
        return instance_id

    if ec2_client is None:
        raise RuntimeError(f"No instance_id in asset metadata for {asset_id} and no EC2 client to fall back to")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: ec2_client.describe_instances(
            Filters=[{"Name": "tag:nexplane-asset-id", "Values": [asset_id]}]
        ),
    )
    reservations = result.get("Reservations", [])
    if not reservations:
        raise RuntimeError(f"No EC2 instance found for asset {asset_id} (no tag and no metadata)")
    return reservations[0]["Instances"][0]["InstanceId"]


async def _stop_source(source_id: str, connector) -> None:
    """Stop source: EC2 stop_instances, or on-prem shutdown via run_command."""
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        loop = asyncio.get_running_loop()
        instance_id = await _instance_id_for_asset(source_id, ec2)
        await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
        await loop.run_in_executor(
            None,
            lambda: ec2.get_waiter("instance_stopped").wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            ),
        )
    else:
        try:
            # nohup + sleep lets the agent respond before the shutdown takes effect
            await dispatch_agent_job(
                "run_command",
                {"command": "nohup sh -c 'sleep 3 && shutdown -h now' </dev/null >/dev/null 2>&1 &", "timeout": 5},
                [source_id],
                timeout_seconds=90,
            )
        except Exception as exc:
            logger.warning(f"[linux_parallel_upgrade] _stop_source: shutdown returned error (instance may be shutting down): {exc}")


async def _cutover_eip(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    creds = await _get_aws_creds(connector)
    ec2 = _make_ec2_client(creds)
    loop = asyncio.get_running_loop()
    eip_id = cutover_config["eip_allocation_id"]

    if not reverse:
        addr = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
        if addr.get("AssociationId"):
            await loop.run_in_executor(
                None, lambda: ec2.disassociate_address(AssociationId=addr["AssociationId"])
            )
        dest_instance_id = await _instance_id_for_asset(dest_id, ec2)
        await loop.run_in_executor(
            None,
            lambda: ec2.associate_address(AllocationId=eip_id, InstanceId=dest_instance_id),
        )
    else:
        addr = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
        if addr.get("AssociationId"):
            await loop.run_in_executor(
                None, lambda: ec2.disassociate_address(AssociationId=addr["AssociationId"])
            )
        source_instance_id = await _instance_id_for_asset(source_id, ec2)
        await loop.run_in_executor(
            None,
            lambda: ec2.associate_address(AllocationId=eip_id, InstanceId=source_instance_id),
        )


async def _cutover_alb(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    import boto3
    creds = await _get_aws_creds(connector)
    region = creds.get("region", "us-east-1")
    elbv2 = boto3.client(
        "elbv2",
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        aws_session_token=creds.get("session_token"),
        region_name=region,
    )
    loop = asyncio.get_running_loop()
    tg_arn = cutover_config["target_group_arn"]
    ec2 = _make_ec2_client(creds)

    if not reverse:
        new_id = await _instance_id_for_asset(dest_id, ec2)
        old_id = await _instance_id_for_asset(source_id, ec2)
        await loop.run_in_executor(
            None, lambda: elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}])
        )
        waiter = elbv2.get_waiter("target_in_service")
        await loop.run_in_executor(
            None,
            lambda: waiter.wait(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}],
                                WaiterConfig={"Delay": 10, "MaxAttempts": 30}),
        )
        await loop.run_in_executor(
            None, lambda: elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=[{"Id": old_id}])
        )
    else:
        new_id = await _instance_id_for_asset(source_id, ec2)
        old_id = await _instance_id_for_asset(dest_id, ec2)
        await loop.run_in_executor(
            None, lambda: elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}])
        )
        waiter = elbv2.get_waiter("target_in_service")
        await loop.run_in_executor(
            None,
            lambda: waiter.wait(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}],
                                WaiterConfig={"Delay": 10, "MaxAttempts": 30}),
        )
        await loop.run_in_executor(
            None, lambda: elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=[{"Id": old_id}])
        )


async def _cutover_dns(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    import boto3
    creds = await _get_aws_creds(connector)
    r53 = boto3.client(
        "route53",
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        aws_session_token=creds.get("session_token"),
    )
    loop = asyncio.get_running_loop()
    hosted_zone_id = cutover_config["hosted_zone_id"]
    record_name = cutover_config["record_name"]
    record_type = cutover_config["record_type"]
    ttl = cutover_config.get("ttl", 300)

    async def _get_host_ip(asset_id: str) -> str:
        result = await dispatch_agent_job(
            "run_command",
            {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
            [asset_id],
            timeout_seconds=90,
        )
        parts = result.get("output", "").strip().split()
        if not parts:
            raise RuntimeError(f"Could not determine IP for asset {asset_id}")
        return parts[0]

    target_id = dest_id if not reverse else source_id
    target_ip = await _get_host_ip(target_id)

    await loop.run_in_executor(
        None,
        lambda: r53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": record_type,
                        "TTL": 60 if not reverse else ttl,
                        "ResourceRecords": [{"Value": target_ip}],
                    },
                }]
            },
        ),
    )


async def _cutover_static_ip(source_id: str, dest_id: str, cutover_config: dict, reverse: bool = False) -> None:
    interface = cutover_config["interface"]
    ip = cutover_config["ip"]
    netmask = cutover_config["netmask"]
    gateway = cutover_config["gateway"]

    add_target = dest_id if not reverse else source_id
    remove_target = source_id if not reverse else dest_id

    add_cmd = f"ip addr add {ip}/{netmask} dev {interface} && ip route add default via {gateway} || true"
    remove_cmd = f"ip addr del {ip}/{netmask} dev {interface} || true"

    await dispatch_agent_job("run_command", {"command": add_cmd, "timeout": 15}, [add_target], timeout_seconds=90)
    await dispatch_agent_job("run_command", {"command": remove_cmd, "timeout": 15}, [remove_target], timeout_seconds=90)


async def _do_cutover(source_id: str, dest_id: str, parameters: dict, connector, reverse: bool = False) -> None:
    method = parameters["cutover_method"]
    config = parameters["cutover_config"]
    if method == "eip":
        await _cutover_eip(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "alb":
        await _cutover_alb(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "dns":
        await _cutover_dns(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "static_ip":
        await _cutover_static_ip(source_id, dest_id, config, reverse=reverse)
    else:
        raise RuntimeError(f"Unknown cutover_method: {method!r}")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1-6: preflight → snapshot → sync → health check → cutover → decommission scheduling."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    pre_sync_runs = parameters.get("pre_sync_runs", 1)
    dry_run = parameters.get("dry_run", False)

    execution_result = {
        "source_asset_id": source_id,
        "dest_asset_id": dest_id,
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

        # Phase 4 — Verify dest health
        logger.info("[linux_parallel_upgrade] Phase 4: verify dest health")
        dest_ip_result = await dispatch_agent_job(
            "run_command",
            {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
            [dest_id],
            timeout_seconds=90,
        )
        dest_ip_raw = dest_ip_result.get("output", "").strip().split()
        dest_ip = dest_ip_raw[0] if dest_ip_raw else ""
        await _verify_dest_health(parameters, dest_ip)

        # Phase 5 — Cutover
        logger.info("[linux_parallel_upgrade] Phase 5: cutover")
        # Final rsync run (the Nth run)
        final_sync = await _run_rsync(source_id, dest_id, parameters)
        execution_result["sync_runs"].append(final_sync)

        await _stop_source(source_id, connector)
        execution_result["source_stopped"] = True

        await _do_cutover(source_id, dest_id, parameters, connector)
        execution_result["cutover_completed"] = True
        logger.info("[linux_parallel_upgrade] Phase 5: cutover complete")

        # Phase 6 — Hold / schedule decommission
        logger.info("[linux_parallel_upgrade] Phase 6: decommission scheduling")
        await _phase6_decommission(parameters, execution_result, connector)
        return execution_result

    except Exception as exc:
        logger.exception(f"[linux_parallel_upgrade] execute failed: {exc}")
        execution_result["error"] = str(exc)
        return execution_result


def _get_scheduler():
    from app.services.recurring_job_service import _scheduler
    return _scheduler


async def _phase6_decommission(parameters: dict, execution_result: dict, connector) -> dict:
    """Phase 6: schedule decommission or record manual-only.

    TODO: APScheduler jobs are in-process only and do not survive a server restart.
    For durability, the scheduled decommission should be persisted via RecurringJob
    service (DB-backed) so it survives restarts. This is deferred — out of scope for
    this commit; tracked as a known limitation.
    """
    source_id = parameters["source_asset_id"]
    hours = parameters.get("decommission_after_hours", 24)

    if hours == 0:
        execution_result["decommission_manual"] = True
        execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_FULL
        return execution_result

    from apscheduler.triggers.date import DateTrigger
    job_id = str(uuid.uuid4())
    fire_time = datetime.now(timezone.utc) + timedelta(hours=hours)

    # Capture snapshot_id at schedule time — not at fire time — so that a rollback
    # that clears execution_result["snapshot_id"] between now and fire time doesn't
    # cause the wrong snapshot_id to be used.
    _snapshot_id = execution_result.get("snapshot_id")

    scheduler = _get_scheduler()
    scheduler.add_job(
        lambda: asyncio.ensure_future(_terminate_source(source_id, _snapshot_id, connector, execution_result)),
        DateTrigger(run_date=fire_time),
        id=f"decommission_{job_id}",
    )
    execution_result["decommission_job_id"] = job_id
    execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_FULL
    logger.info(f"[linux_parallel_upgrade] decommission scheduled in {hours}h — job_id={job_id}")
    return execution_result


async def _terminate_source(source_id: str, snapshot_id, connector, execution_result: dict | None = None) -> None:
    """Terminate source instance and delete snapshot. Marks rollback irreversible.

    NOTE: The in-process execution_result dict is updated so that callers in the
    same process see the irreversible marker immediately. For server-restart
    durability, a separate CR state update to the database would be required —
    that is a known limitation and is not implemented here.
    """
    if execution_result is not None:
        execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_IRREVERSIBLE
    logger.info(f"[linux_parallel_upgrade] decommission: terminating source {source_id}")
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        loop = asyncio.get_running_loop()
        try:
            instance_id = await _instance_id_for_asset(source_id, ec2)
            await loop.run_in_executor(None, lambda: ec2.terminate_instances(InstanceIds=[instance_id]))
        except Exception as exc:
            logger.warning(f"[linux_parallel_upgrade] _terminate_source: could not find/terminate instance for {source_id}: {exc}")
        if snapshot_id:
            try:
                await loop.run_in_executor(None, lambda: ec2.delete_snapshot(SnapshotId=snapshot_id))
            except Exception as exc:
                logger.warning(f"[linux_parallel_upgrade] Failed to delete snapshot {snapshot_id}: {exc}")
    else:
        try:
            await dispatch_agent_job(
                "run_command",
                {"command": "nohup sh -c 'sleep 3 && shutdown -h now' </dev/null >/dev/null 2>&1 &", "timeout": 5},
                [source_id],
                timeout_seconds=90,
            )
        except Exception as exc:
            logger.warning(f"[linux_parallel_upgrade] _terminate_source: shutdown returned error (instance may be shutting down): {exc}")


async def _start_source(source_id: str, connector) -> None:
    """Restart stopped source instance (EC2 or on-prem)."""
    import boto3 as _boto3
    loop = asyncio.get_running_loop()

    # Build EC2 client: prefer connector creds, fall back to platform IAM role.
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
    else:
        ec2 = _boto3.client("ec2", region_name="us-east-1")

    try:
        instance_id = await _instance_id_for_asset(source_id, ec2)
        await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
        await loop.run_in_executor(
            None,
            lambda: ec2.get_waiter("instance_running").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
            ),
        )
    except Exception as exc:
        logger.warning(f"[linux_parallel_upgrade] _start_source: could not find/start instance for {source_id}: {exc}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Reverse cutover, restart source, cancel decommission job."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    rollback_result: dict = {"actions": []}

    try:
        # Cancel decommission job if pending
        job_id = execution_result.get("decommission_job_id")
        if job_id:
            try:
                scheduler = _get_scheduler()
                scheduler.remove_job(f"decommission_{job_id}")
                rollback_result["actions"].append("cancelled_decommission_job")
            except Exception as exc:
                logger.warning(f"[linux_parallel_upgrade] Could not cancel decommission job {job_id}: {exc}")

        # If source was stopped, restart it first so traffic repoint hits a live host
        if execution_result.get("source_stopped"):
            await _start_source(source_id, connector)
            # After EC2 start, allow extra time for agent to boot and register
            await _check_agent(source_id, timeout_seconds=300)
            rollback_result["actions"].append("restarted_source")

        # If cutover completed, reverse traffic (source is now running)
        if execution_result.get("cutover_completed"):
            await _do_cutover(source_id, dest_id, parameters, connector, reverse=True)
            rollback_result["actions"].append("reversed_cutover")

    except Exception as exc:
        logger.exception(f"[linux_parallel_upgrade] rollback failed: {exc}")
        rollback_result["error"] = str(exc)

    return rollback_result
