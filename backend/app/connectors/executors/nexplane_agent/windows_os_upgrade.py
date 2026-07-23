# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Windows Server in-place upgrade executor.
Flow: preflight -> snapshot -> upgrade (restart + agent poll) -> verify -> (rollback on failure or operator request).
Reuses _restore_snapshot / _get_aws_creds / _make_ec2_client from os_upgrade.py for the EC2 EBS rollback path.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.database import AsyncSessionLocal
from app.connectors.executors.nexplane_agent.os_upgrade import (  # noqa: E402
    _restore_snapshot,
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Supported upgrade paths. Direct 2016→2022 is NOT supported (must go via 2019).
_SUPPORTED_PATHS = {
    ("2016", "2019"),
    ("2019", "2022"),
}

# Pre-flight hard-fail thresholds
_MIN_DISK_FREE_GB = 32.0
# DISM compat exit codes
_DISM_PASS = "0xC1900210"        # no issues
_DISM_SOFT_WARN = "0xC1900208"   # soft warning — proceed


def _detect_current_version(current_os: str) -> str:
    """Extract major version string from Win32_OperatingSystem Caption."""
    for ver in ("2022", "2019", "2016", "2012"):
        if ver in current_os:
            return ver
    return "unknown"


def _check_upgrade_path(current_os: str, target_version: str) -> str | None:
    """Return None if path is supported, else an error message."""
    current_ver = _detect_current_version(current_os)
    if current_ver == target_version:
        return f"Already running Windows Server {target_version} — upgrade not needed"
    if (current_ver, target_version) not in _SUPPORTED_PATHS:
        return (
            f"Direct upgrade from {current_ver} to {target_version} is not supported. "
            f"Supported paths: 2016→2019, 2019→2022. "
            f"If running 2016, upgrade to 2019 first, then 2019→2022."
        )
    return None


def _preflight_hard_fail(preflight: dict, target_version: str) -> str | None:
    """Return an error message if the pre-flight has a hard failure, else None."""
    if not preflight.get("disk_ok", True) or preflight.get("disk_free_gb", 999) < _MIN_DISK_FREE_GB:
        gb = preflight.get("disk_free_gb", "unknown")
        return f"Disk free ({gb} GB) is below the required {_MIN_DISK_FREE_GB} GB minimum for Windows in-place upgrade"

    path_err = _check_upgrade_path(preflight.get("current_os", ""), target_version)
    if path_err:
        return path_err

    dism_exit = preflight.get("dism_exit_code", _DISM_PASS)
    if preflight.get("dism_compat_passed") is False and dism_exit not in (_DISM_PASS, _DISM_SOFT_WARN):
        issues = preflight.get("dism_compat_issues", [])
        return f"DISM compatibility check failed (exit {dism_exit}): {issues}"

    return None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    target_version = parameters.get("target_version", "")
    if not target_version:
        raise ValueError("target_version is required (e.g. '2022')")

    dry_run = bool(parameters.get("dry_run", False))
    skip_snapshot = bool(parameters.get("skip_snapshot", False))
    iso_path = parameters.get("iso_path") or None
    health_check_command = parameters.get("health_check_command") or None

    from app.models.asset import Asset

    # ------------------------------------------------------------------
    # Phase 1: Pre-flight
    # ------------------------------------------------------------------
    logger.info(f"[windows_os_upgrade] Pre-flight for asset {asset_id}, target={target_version}")
    preflight = await dispatch_agent_job(
        command="windows_preflight_os_upgrade",
        parameters={"target_version": target_version, "iso_path": iso_path},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    fail_reason = _preflight_hard_fail(preflight, target_version)
    if fail_reason:
        logger.warning(f"[windows_os_upgrade] Pre-flight hard fail: {fail_reason}")
        return {
            "status": "preflight_failed",
            "reason": fail_reason,
            "preflight": preflight,
            "asset_id": asset_id,
        }

    if dry_run:
        logger.info(f"[windows_os_upgrade] dry_run — returning pre-flight report only")
        return {
            "status": "dry_run",
            "preflight": preflight,
            "asset_id": asset_id,
            "target_version": target_version,
            "snapshot_id": None,
        }

    # ------------------------------------------------------------------
    # Phase 2: Snapshot
    # ------------------------------------------------------------------
    snapshot_meta = None
    snapshot_id = None

    if skip_snapshot:
        # Enforce production guard
        async with AsyncSessionLocal() as db:
            try:
                asset_key = uuid.UUID(asset_id)
            except (ValueError, AttributeError):
                asset_key = asset_id
            asset = await db.get(Asset, asset_key)
            env = (asset.asset_metadata or {}).get("environment", "") if asset else ""
        if env == "prod":
            raise RuntimeError(
                "skip_snapshot=True is not permitted on prod assets. "
                "Set skip_snapshot=False or demote the asset environment."
            )
        logger.warning(f"[windows_os_upgrade] Skipping snapshot for non-prod asset {asset_id}")
    else:
        logger.info(f"[windows_os_upgrade] Taking pre-upgrade snapshot for {asset_id}")
        try:
            async with AsyncSessionLocal() as db:
                try:
                    asset_key = uuid.UUID(asset_id)
                except (ValueError, AttributeError):
                    asset_key = asset_id
                asset = await db.get(Asset, asset_key)
                meta = asset.asset_metadata or {} if asset else {}
                cloud_instance_id = meta.get("instance_id")
                organization_id = asset.organization_id if asset else None

            if cloud_instance_id:
                # EC2 path — EBS snapshot
                snapshot_meta = await _take_snapshot(asset_id, cloud_instance_id, connector, organization_id)
                snapshot_meta["snapshot_type"] = "ebs"
                snapshot_id = snapshot_meta["snapshot_id"]
                logger.info(f"[windows_os_upgrade] EBS snapshot created: {snapshot_id}")
            else:
                # On-prem agent path — VSS shadow copy
                logger.info(f"[windows_os_upgrade] No EC2 instance_id — using VSS shadow copy")
                vss_result = await dispatch_agent_job(
                    command="windows_vss_create_shadow",
                    parameters={"volume": "C:"},
                    asset_ids=[asset_id],
                    timeout_seconds=120,
                )
                snapshot_meta = {
                    "snapshot_type": "vss",
                    "shadow_copy_id": vss_result.get("shadow_copy_id"),
                    "shadow_device": vss_result.get("shadow_device"),
                }
                snapshot_id = vss_result.get("shadow_copy_id")
                logger.info(f"[windows_os_upgrade] VSS shadow copy created: {snapshot_id}")

        except Exception as e:
            logger.warning(f"[windows_os_upgrade] Snapshot failed: {e}")
            async with AsyncSessionLocal() as db:
                try:
                    asset_key = uuid.UUID(asset_id)
                except (ValueError, AttributeError):
                    asset_key = asset_id
                asset = await db.get(Asset, asset_key)
                env = (asset.asset_metadata or {}).get("environment", "") if asset else ""
            if env == "prod":
                raise RuntimeError(
                    f"Pre-upgrade snapshot failed on prod asset: {e}. "
                    "Set skip_snapshot=True to bypass (non-prod only)."
                )

    # ------------------------------------------------------------------
    # Phase 3: Upgrade (launch setup.exe + poll for agent re-registration)
    # ------------------------------------------------------------------
    logger.info(f"[windows_os_upgrade] Starting upgrade for {asset_id}, target={target_version}")
    upgrade_start_time = datetime.now(timezone.utc).isoformat()

    try:
        await dispatch_agent_job(
            command="windows_start_os_upgrade",
            parameters={
                "target_version": target_version,
                "iso_path": iso_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=120,  # setup.exe launch returns quickly; actual upgrade is async
        )
    except Exception as e:
        logger.error(f"[windows_os_upgrade] setup.exe launch failed for {asset_id}: {e}")
        return {
            "status": "failed",
            "reason": f"Upgrade launch failed: {e}",
            "phase": "upgrade",
            "snapshot_id": snapshot_id,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
        }

    # Poll for agent re-registration after restart
    logger.info(f"[windows_os_upgrade] Polling for agent re-registration on {asset_id} (30 min timeout)")
    agent_reconnected, elapsed = await _poll_agent_reconnect(asset_id, timeout_seconds=1800, interval_seconds=30)

    if not agent_reconnected:
        return {
            "status": "failed",
            "reason": "agent_reconnect_timeout — upgrade may have succeeded; check the instance directly",
            "phase": "upgrading",
            "upgrade_start_time": upgrade_start_time,
            "agent_reconnected": False,
            "agent_reconnect_elapsed_seconds": elapsed,
            "snapshot_id": snapshot_id,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
        }

    logger.info(f"[windows_os_upgrade] Agent re-registered after {elapsed}s on {asset_id}")

    # ------------------------------------------------------------------
    # Phase 4: Post-Upgrade Verification
    # ------------------------------------------------------------------
    logger.info(f"[windows_os_upgrade] Verifying upgrade for {asset_id}")
    verify_result = await dispatch_agent_job(
        command="windows_verify_os_upgrade",
        parameters={
            "target_version": target_version,
            "pre_upgrade_services": preflight.get("critical_services", []),
            "health_check_command": health_check_command,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    post_upgrade_os = verify_result.get("post_upgrade_os", "")
    if target_version not in post_upgrade_os:
        return {
            "status": "failed",
            "reason": f"OS version mismatch after upgrade: expected {target_version}, got {post_upgrade_os!r}",
            "phase": "verifying",
            "snapshot_id": snapshot_id,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
            "verify_result": verify_result,
        }

    health_check_passed = verify_result.get("health_check_passed", True)
    if health_check_command and not health_check_passed:
        return {
            "status": "failed",
            "reason": "health_check_command returned non-zero — upgrade completed but application is not healthy",
            "phase": "verifying",
            "snapshot_id": snapshot_id,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
            "verify_result": verify_result,
            "health_check_passed": False,
            "health_check_output": verify_result.get("health_check_output"),
        }

    # Update asset metadata
    try:
        from app.models.asset import Asset
        async with AsyncSessionLocal() as db:
            try:
                asset_key = uuid.UUID(asset_id)
            except (ValueError, AttributeError):
                asset_key = asset_id
            asset = await db.get(Asset, asset_key)
            if asset:
                meta = dict(asset.asset_metadata or {})
                meta["os_version"] = post_upgrade_os
                meta["os_upgrade_at"] = datetime.now(timezone.utc).isoformat()
                asset.asset_metadata = meta
                await db.commit()
    except Exception as e:
        logger.warning(f"[windows_os_upgrade] Failed to update asset metadata: {e}")

    return {
        "status": "completed",
        "asset_id": asset_id,
        "current_os": preflight.get("current_os"),
        "target_version": target_version,
        "phase": "complete",
        "preflight": preflight,
        "snapshot_id": snapshot_id,
        "snapshot_meta": snapshot_meta,
        "upgrade_start_time": upgrade_start_time,
        "agent_reconnected": agent_reconnected,
        "agent_reconnect_elapsed_seconds": elapsed,
        "post_upgrade_os": post_upgrade_os,
        "health_check_passed": health_check_passed,
        "health_check_output": verify_result.get("health_check_output"),
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _poll_agent_reconnect(asset_id: str, timeout_seconds: int = 1800, interval_seconds: int = 30):
    """Poll dispatch_agent_job health_check until online or timeout. Returns (reconnected, elapsed_seconds)."""
    start = asyncio.get_event_loop().time()
    deadline = start + timeout_seconds

    while asyncio.get_event_loop().time() < deadline:
        try:
            await dispatch_agent_job(
                command="health_check",
                parameters={},
                asset_ids=[asset_id],
                timeout_seconds=20,
            )
            elapsed = int(asyncio.get_event_loop().time() - start)
            return True, elapsed
        except Exception:
            await asyncio.sleep(interval_seconds)

    elapsed = int(asyncio.get_event_loop().time() - start)
    return False, elapsed


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id")
    snapshot_meta = execution_result.get("snapshot_meta") or {}
    asset_id = execution_result.get("asset_id") or (
        str(parameters.get("asset_ids", [None])[0]) if parameters.get("asset_ids") else None
    )

    if not snapshot_id:
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    snapshot_type = snapshot_meta.get("snapshot_type", "ebs")

    if snapshot_type == "vss":
        return await _rollback_vss(asset_id, snapshot_id, snapshot_meta)

    # EC2 EBS rollback — reuse os_upgrade._restore_snapshot
    if not snapshot_meta.get("instance_id"):
        return {
            "rolled_back": False,
            "reason": "no_snapshot_meta — CR predates automated rollback; restore manually",
            "snapshot_id": snapshot_id,
            "manual_steps": [
                "1. Stop the EC2 instance",
                f"2. aws ec2 create-volume --snapshot-id {snapshot_id} --availability-zone <az>",
                "3. Detach current root volume",
                "4. Attach new volume as root device",
                "5. Start instance",
            ],
        }

    organization_id = None
    if asset_id:
        from app.models.asset import Asset
        try:
            async with AsyncSessionLocal() as db:
                try:
                    asset_key = uuid.UUID(asset_id)
                except (ValueError, AttributeError):
                    asset_key = asset_id
                asset = await db.get(Asset, asset_key)
                organization_id = asset.organization_id if asset else None
        except Exception:
            pass

    try:
        result = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector, organization_id)
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc), "snapshot_id": snapshot_id}

    return {
        "rolled_back": result.get("restored", False),
        "snapshot_id": snapshot_id,
        "new_volume_id": result.get("new_volume_id"),
        "old_volume_id": result.get("old_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
    }


async def _rollback_vss(asset_id: str, snapshot_id: str, snapshot_meta: dict) -> dict:
    """
    VSS system volume restore cannot run while the OS is live.
    Attempt wbadmin restore; if unavailable, return manual steps.
    """
    shadow_copy_id = snapshot_meta.get("shadow_copy_id", snapshot_id)
    shadow_device = snapshot_meta.get("shadow_device", "")

    # Attempt automated wbadmin restore
    try:
        result = await dispatch_agent_job(
            command="windows_vss_restore",
            parameters={"shadow_copy_id": shadow_copy_id, "volume": "C:"},
            asset_ids=[asset_id],
            timeout_seconds=3600,
        )
        if result.get("restored"):
            return {"rolled_back": True, "shadow_copy_id": shadow_copy_id}
    except Exception as e:
        logger.warning(f"[windows_os_upgrade] Automated VSS restore failed: {e}")

    # Fall back to manual steps
    return {
        "rolled_back": False,
        "reason": "vss_system_volume_requires_offline_restore",
        "shadow_copy_id": shadow_copy_id,
        "shadow_device": shadow_device,
        "manual_steps": [
            "Boot the server from Windows installation media or WinPE",
            "At recovery prompt: rstrui.exe or wbadmin start recovery",
            f"Select shadow copy: {shadow_copy_id}",
            "Restore C: volume and restart",
        ],
    }
