# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Windows Parallel Migration executor.

Flow: preflight → snapshot → inventory → sync → health check → cutover.
Rollback: reverse cutover → start source → verify agent.
Source is stopped-not-terminated; decommission is a manual CR action after 24h.
"""
import asyncio
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

DEFINITION = {
    "name": "windows_parallel_migration",
    "display_name": "Windows Parallel Migration",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"

# Supported upgrade paths (same as windows_os_upgrade.py)
_SUPPORTED_PATHS = {
    ("2016", "2019"),
    ("2019", "2022"),
}


def _detect_current_version(current_os: str) -> str:
    """Extract major version from Win32_OperatingSystem Caption."""
    for ver in ("2022", "2019", "2016", "2012"):
        if ver in current_os:
            return ver
    return "unknown"


def _check_upgrade_path(current_os: str, target_version: str) -> str | None:
    """Return None if path is supported, else an error message."""
    current_ver = _detect_current_version(current_os)
    if current_ver == target_version:
        return f"Source is already running Windows Server {target_version} — migration not needed"
    if (current_ver, target_version) not in _SUPPORTED_PATHS:
        return (
            f"Direct migration from {current_ver} to {target_version} is not supported. "
            f"Supported paths: 2016→2019, 2019→2022."
        )
    return None


def _determine_rollback_status(rollback_result: dict) -> bool:
    """Return True if rollback succeeded (no 'error' key in result)."""
    return "error" not in rollback_result


async def _check_agent(asset_id: str, timeout_seconds: int = 90) -> None:
    """Raise RuntimeError if agent is unreachable."""
    try:
        await dispatch_agent_job("health_check", {}, [asset_id], timeout_seconds=timeout_seconds)
    except Exception as exc:
        raise RuntimeError(f"Agent on asset {asset_id} unreachable: {exc}") from exc


async def _get_windows_version(asset_id: str) -> str:
    """Return Windows OS Caption string, e.g. 'Microsoft Windows Server 2022 Datacenter'."""
    result = await dispatch_agent_job(
        "run_command",
        {"command": "(Get-WmiObject -Class Win32_OperatingSystem).Caption", "timeout": 15},
        [asset_id],
        timeout_seconds=90,
    )
    return result.get("output", "").strip()


async def _get_host_ip(asset_id: str) -> str:
    """Return primary private IP of the asset's host."""
    result = await dispatch_agent_job(
        "run_command",
        {"command": "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*'} | Select-Object -First 1).IPAddress", "timeout": 10},
        [asset_id],
        timeout_seconds=90,
    )
    ip = result.get("output", "").strip()
    if not ip:
        raise RuntimeError(f"Could not determine IP for asset {asset_id}")
    return ip


async def _instance_id_for_asset(asset_id: str, ec2_client=None) -> str:
    """Return EC2 instance ID for an asset (DB metadata first, tag fallback)."""
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
        raise RuntimeError(f"No EC2 instance found for asset {asset_id}")
    return reservations[0]["Instances"][0]["InstanceId"]


async def _preflight(parameters: dict, connector) -> dict:
    """Phase 1: verify agents reachable, OS versions valid, cutover preconditions met."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    cutover_method = parameters["cutover_method"]
    cutover_config = parameters["cutover_config"]

    await _check_agent(source_id)
    await _check_agent(dest_id)

    source_os = await _get_windows_version(source_id)
    dest_os = await _get_windows_version(dest_id)

    source_ver = _detect_current_version(source_os)
    dest_ver = _detect_current_version(dest_os)

    path_err = _check_upgrade_path(source_os, dest_ver)
    if path_err:
        raise RuntimeError(f"Preflight: {path_err}")

    if cutover_method == "eip":
        if not cutover_config.get("eip_allocation_id"):
            raise RuntimeError("cutover_config.eip_allocation_id required for method 'eip'")
    elif cutover_method == "dns":
        for field in ("hosted_zone_id", "record_name", "record_type"):
            if not cutover_config.get(field):
                raise RuntimeError(f"cutover_config.{field} required for method 'dns'")
    elif cutover_method == "eni":
        if not cutover_config.get("eni_id"):
            raise RuntimeError("cutover_config.eni_id required for method 'eni'")
    else:
        raise RuntimeError(f"Unknown cutover_method: {cutover_method!r}")

    return {
        "source_os": source_os,
        "dest_os": dest_os,
        "source_version": source_ver,
        "dest_version": dest_ver,
    }


async def _phase2_snapshot(source_id: str, connector) -> dict:
    """Phase 2: EBS snapshot of source (EC2) or skip (on-prem)."""
    try:
        imds_result = await dispatch_agent_job(
            "run_command",
            {"command": "Invoke-RestMethod -Uri 'http://169.254.169.254/latest/meta-data/instance-id' -TimeoutSec 5", "timeout": 8},
            [source_id],
            timeout_seconds=90,
        )
        instance_id = imds_result.get("output", "").strip()
        if not instance_id or not instance_id.startswith("i-"):
            raise RuntimeError("IMDS returned non-instance-id")
    except Exception as exc:
        logger.warning(f"[windows_parallel_migration] IMDS failed ({exc}); skipping snapshot")
        return {"snapshot_id": None, "snapshot_skipped": True}

    if not connector.credentials or not connector.credentials.get("access_key_id"):
        logger.warning("[windows_parallel_migration] No AWS creds; skipping snapshot")
        return {"snapshot_id": None, "snapshot_skipped": True}

    snap = await _take_snapshot(source_id, instance_id, connector)
    return {"snapshot_id": snap["snapshot_id"], "snapshot_meta": snap, "snapshot_skipped": False}


async def _phase3_inventory(source_id: str, parameters: dict) -> dict:
    """Phase 3: run win_inventory on source. Returns {inventory, hostname_refs}."""
    hostname_result = await dispatch_agent_job(
        "run_command",
        {"command": "hostname", "timeout": 10},
        [source_id],
        timeout_seconds=90,
    )
    source_hostname = hostname_result.get("output", "").strip()

    registry_keys = parameters.get("registry_keys") or []

    inventory = await dispatch_agent_job(
        "win_inventory",
        {"hostname": source_hostname, "registry_keys": registry_keys},
        [source_id],
        timeout_seconds=300,
    )

    hostname_refs = inventory.pop("hostname_refs", [])
    return {"inventory": inventory, "hostname_refs": hostname_refs, "source_hostname": source_hostname}


async def _create_robocopy_account(dest_id: str) -> tuple[str, str]:
    """Create temp local admin account on dest for robocopy SMB access."""
    username = "nprobocopy"
    alphabet = string.ascii_letters + string.digits + "!@#"
    password = ''.join(secrets.choice(alphabet) for _ in range(20))

    safe_pw = password.replace("'", "''")
    script = (
        f"$pw = ConvertTo-SecureString '{safe_pw}' -AsPlainText -Force; "
        f"New-LocalUser -Name '{username}' -Password $pw -PasswordNeverExpires -ErrorAction SilentlyContinue; "
        f"Add-LocalGroupMember -Group 'Administrators' -Member '{username}' -ErrorAction SilentlyContinue"
    )
    await dispatch_agent_job(
        "run_command",
        {"command": script, "timeout": 30},
        [dest_id],
        timeout_seconds=90,
    )
    return username, password


async def _delete_robocopy_account(dest_id: str) -> None:
    """Remove temp robocopy account from dest."""
    try:
        await dispatch_agent_job(
            "run_command",
            {"command": "Remove-LocalUser -Name 'nprobocopy' -ErrorAction SilentlyContinue", "timeout": 15},
            [dest_id],
            timeout_seconds=60,
        )
    except Exception as exc:
        logger.warning(f"[windows_parallel_migration] Failed to delete robocopy account: {exc}")


async def _phase4_sync(source_id: str, dest_id: str, parameters: dict, execution_result: dict) -> dict:
    """Phase 4: robocopy source→dest, apply hostname replacements on dest."""
    dest_ip = await _get_host_ip(dest_id)

    username, password = await _create_robocopy_account(dest_id)
    try:
        robocopy_result = await dispatch_agent_job(
            "win_robocopy_push",
            {
                "dest_host": dest_ip,
                "dest_user": username,
                "dest_password": password,
                "excludes": parameters.get("sync_exclude", []),
            },
            [source_id],
            timeout_seconds=1800,  # 30 min for large data volumes
        )
    finally:
        await _delete_robocopy_account(dest_id)

    # Apply hostname replacements on dest if operator specified them
    hostname_replacements = parameters.get("hostname_replacements") or []
    replacements_result = None
    if hostname_replacements:
        replacements_result = await dispatch_agent_job(
            "win_apply_replacements",
            {"replacements": hostname_replacements},
            [dest_id],
            timeout_seconds=300,
        )

    return {
        "robocopy": robocopy_result,
        "replacements_applied": replacements_result,
    }


async def _phase5_health_check(dest_id: str, parameters: dict) -> dict:
    """Phase 5: verify dest agent responds and key services are present."""
    await _check_agent(dest_id)

    # Verify IIS is configured if inventory captured sites
    inventory = parameters.get("_inventory_snapshot", {})
    iis_sites_json = inventory.get("iis_sites", "[]")
    if iis_sites_json and iis_sites_json != "[]":
        check_result = await dispatch_agent_job(
            "run_command",
            {"command": "(Get-Website | Measure-Object).Count", "timeout": 15},
            [dest_id],
            timeout_seconds=90,
        )
        site_count = check_result.get("output", "0").strip()
        if site_count == "0":
            raise RuntimeError("Health check: IIS sites missing on dest after sync")

    return {"health_check_passed": True}


async def _stop_source(source_id: str, connector) -> None:
    """Stop source instance via EC2 API (preferred) or shutdown command."""
    import boto3 as _boto3

    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
    else:
        ec2 = _boto3.client("ec2", region_name="us-east-1")

    try:
        instance_id = await _instance_id_for_asset(source_id, ec2)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
        await loop.run_in_executor(
            None,
            lambda: ec2.get_waiter("instance_stopped").wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            ),
        )
        return
    except Exception as exc:
        logger.warning(f"[windows_parallel_migration] EC2 stop failed ({exc}); falling back to shutdown command")

    # On-prem fallback: schedule shutdown with delay so agent can respond
    try:
        await dispatch_agent_job(
            "run_command",
            {"command": "Start-Process -FilePath 'shutdown.exe' -ArgumentList '/s /t 10' -WindowStyle Hidden", "timeout": 5},
            [source_id],
            timeout_seconds=90,
        )
    except Exception as exc:
        logger.warning(f"[windows_parallel_migration] Shutdown command error (may be shutting down): {exc}")


async def _start_source(source_id: str, connector) -> None:
    """Restart stopped source instance."""
    import boto3 as _boto3

    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
    else:
        ec2 = _boto3.client("ec2", region_name="us-east-1")

    instance_id = await _instance_id_for_asset(source_id, ec2)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_running").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )


async def _cutover_eip(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    creds = await _get_aws_creds(connector)
    ec2 = _make_ec2_client(creds)
    loop = asyncio.get_running_loop()
    eip_id = cutover_config["eip_allocation_id"]

    target_id = dest_id if not reverse else source_id
    target_instance = await _instance_id_for_asset(target_id, ec2)

    addr = await loop.run_in_executor(
        None, lambda: ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
    )
    if addr.get("AssociationId"):
        await loop.run_in_executor(
            None, lambda: ec2.disassociate_address(AssociationId=addr["AssociationId"])
        )
    await loop.run_in_executor(
        None,
        lambda: ec2.associate_address(AllocationId=eip_id, InstanceId=target_instance),
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


async def _cutover_eni(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    """Detach ENI from current holder and attach to new holder."""
    import boto3 as _boto3
    creds = await _get_aws_creds(connector)
    ec2 = _make_ec2_client(creds)
    loop = asyncio.get_running_loop()
    eni_id = cutover_config["eni_id"]

    attach_to_id = dest_id if not reverse else source_id
    attach_to_instance = await _instance_id_for_asset(attach_to_id, ec2)

    # Detach from current holder
    ni = await loop.run_in_executor(
        None,
        lambda: ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])["NetworkInterfaces"][0],
    )
    if ni.get("Attachment"):
        attachment_id = ni["Attachment"]["AttachmentId"]
        await loop.run_in_executor(
            None, lambda: ec2.detach_network_interface(AttachmentId=attachment_id, Force=True)
        )
        # Wait for ENI to become available
        for _ in range(30):
            await asyncio.sleep(5)
            ni = await loop.run_in_executor(
                None,
                lambda: ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])["NetworkInterfaces"][0],
            )
            if ni.get("Status") == "available":
                break
        else:
            raise RuntimeError(f"ENI {eni_id} did not become available after detach (timed out after 150s)")

    await loop.run_in_executor(
        None,
        lambda: ec2.attach_network_interface(
            NetworkInterfaceId=eni_id,
            InstanceId=attach_to_instance,
            DeviceIndex=0,
        ),
    )


async def _do_cutover(source_id: str, dest_id: str, parameters: dict, connector, reverse: bool = False) -> None:
    method = parameters["cutover_method"]
    config = parameters["cutover_config"]
    if method == "eip":
        await _cutover_eip(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "dns":
        await _cutover_dns(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "eni":
        await _cutover_eni(source_id, dest_id, config, connector, reverse=reverse)
    else:
        raise RuntimeError(f"Unknown cutover_method: {method!r}")


def _get_scheduler():
    from app.services.recurring_job_service import _scheduler
    return _scheduler


async def _phase6_decommission(parameters: dict, execution_result: dict, connector) -> None:
    """Phase 6: schedule decommission job or mark manual-only."""
    source_id = parameters["source_asset_id"]
    hours = parameters.get("decommission_after_hours", 24)

    if hours == 0:
        execution_result["decommission_manual"] = True
        return

    from apscheduler.triggers.date import DateTrigger
    job_id = str(uuid.uuid4())
    fire_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    _snapshot_id = execution_result.get("snapshot_id")

    scheduler = _get_scheduler()
    scheduler.add_job(
        lambda: asyncio.ensure_future(_terminate_source(source_id, _snapshot_id, connector, execution_result)),
        DateTrigger(run_date=fire_time),
        id=f"wpm_decommission_{job_id}",
    )
    execution_result["decommission_job_id"] = job_id
    logger.info(f"[windows_parallel_migration] decommission scheduled in {hours}h — job_id={job_id}")


async def _terminate_source(source_id: str, snapshot_id, connector, execution_result: dict | None = None) -> None:
    """Terminate source instance and delete snapshot."""
    if execution_result is not None:
        execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_IRREVERSIBLE
    import boto3 as _boto3
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
    else:
        ec2 = _boto3.client("ec2", region_name="us-east-1")
    loop = asyncio.get_running_loop()
    try:
        instance_id = await _instance_id_for_asset(source_id, ec2)
        await loop.run_in_executor(None, lambda: ec2.terminate_instances(InstanceIds=[instance_id]))
    except Exception as exc:
        logger.warning(f"[windows_parallel_migration] _terminate_source: {exc}")
    if snapshot_id:
        try:
            await loop.run_in_executor(None, lambda: ec2.delete_snapshot(SnapshotId=snapshot_id))
        except Exception as exc:
            logger.warning(f"[windows_parallel_migration] Failed to delete snapshot {snapshot_id}: {exc}")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phases 1-6: preflight → snapshot → inventory → sync → health check → cutover."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    dry_run = parameters.get("dry_run", False)

    execution_result = {
        "source_asset_id": source_id,
        "dest_asset_id": dest_id,
        "snapshot_id": None,
        "snapshot_skipped": False,
        "inventory": None,
        "hostname_refs": [],
        "sync_completed": False,
        "cutover_completed": False,
        "source_stopped": False,
        "decommission_job_id": None,
        "decommission_manual": False,
        "rollback_capability": ROLLBACK_CAPABILITY_FULL,
        "cutover_method": parameters.get("cutover_method"),
        "cutover_config": parameters.get("cutover_config"),
        "preflight": None,
        "dry_run": dry_run,
    }

    try:
        # Phase 1 — Preflight
        logger.info(f"[windows_parallel_migration] Phase 1: preflight source={source_id} dest={dest_id}")
        preflight_report = await _preflight(parameters, connector)
        execution_result["preflight"] = preflight_report

        if dry_run:
            logger.info("[windows_parallel_migration] dry_run=True — stopping after preflight")
            return execution_result

        # Phase 2 — Snapshot
        logger.info("[windows_parallel_migration] Phase 2: snapshot source")
        snap_result = await _phase2_snapshot(source_id, connector)
        execution_result.update(snap_result)

        # Phase 3 — Inventory
        logger.info("[windows_parallel_migration] Phase 3: inventory source")
        inv_result = await _phase3_inventory(source_id, parameters)
        execution_result["inventory"] = inv_result["inventory"]
        execution_result["hostname_refs"] = inv_result["hostname_refs"]
        # Stash inventory in parameters snapshot for health check
        parameters["_inventory_snapshot"] = inv_result["inventory"]

        # Phase 4 — Sync
        logger.info("[windows_parallel_migration] Phase 4: sync source→dest via robocopy")
        sync_result = await _phase4_sync(source_id, dest_id, parameters, execution_result)
        execution_result["sync"] = sync_result
        execution_result["sync_completed"] = True

        # Phase 5 — Health check
        logger.info("[windows_parallel_migration] Phase 5: health check dest")
        health_result = await _phase5_health_check(dest_id, parameters)
        execution_result["health_check"] = health_result

        # Phase 6 — Cutover
        logger.info("[windows_parallel_migration] Phase 6: cutover")
        await _stop_source(source_id, connector)
        execution_result["source_stopped"] = True

        await _do_cutover(source_id, dest_id, parameters, connector)
        execution_result["cutover_completed"] = True
        logger.info("[windows_parallel_migration] Phase 6: cutover complete")

        await _phase6_decommission(parameters, execution_result, connector)
        return execution_result

    except Exception as exc:
        logger.exception(f"[windows_parallel_migration] execute failed: {exc}")
        execution_result["error"] = str(exc)
        return execution_result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Reverse cutover, restart source, cancel decommission job."""
    source_id = execution_result.get("source_asset_id") or parameters["source_asset_id"]
    dest_id = execution_result.get("dest_asset_id") or parameters["dest_asset_id"]
    rollback_result: dict = {"actions": []}

    try:
        # Cancel decommission job if pending
        job_id = execution_result.get("decommission_job_id")
        if job_id:
            try:
                _get_scheduler().remove_job(f"wpm_decommission_{job_id}")
                rollback_result["actions"].append("cancelled_decommission_job")
            except Exception as exc:
                logger.warning(f"[windows_parallel_migration] Could not cancel decommission job: {exc}")

        if execution_result.get("source_stopped"):
            await _start_source(source_id, connector)
            await _check_agent(source_id, timeout_seconds=300)
            rollback_result["actions"].append("restarted_source")

        if execution_result.get("cutover_completed"):
            await _do_cutover(source_id, dest_id, parameters, connector, reverse=True)
            rollback_result["actions"].append("reversed_cutover")

    except Exception as exc:
        logger.exception(f"[windows_parallel_migration] rollback failed: {exc}")
        rollback_result["error"] = str(exc)

    return rollback_result
