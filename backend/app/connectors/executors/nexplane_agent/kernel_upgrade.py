# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Linux kernel upgrade executor.

Flow: preflight → EBS snapshot → install kernel + set grub-reboot (one-shot) →
      trigger reboot → wait for agent re-registration → verify running kernel →
      verify application services health →
      if ok: done
      if fail: auto-rollback (EBS restore on cloud, grub fallback on bare-metal).

Rollback:
  - Cloud (snapshot available): restore EBS volume, reboot into old kernel
  - Bare-metal (no snapshot): dispatch rollback_kernel_upgrade (grub-set-default + reboot)
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_REREGISTER_TIMEOUT_S = 1800  # 30 minutes — kernel upgrades can take time to boot
_REREGISTER_POLL_S = 15


# ---------------------------------------------------------------------------
# Thin wrappers — allows tests to patch at module level
# ---------------------------------------------------------------------------

async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _d
    return await _d(command=command, parameters=parameters,
                    asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def _get_aws_creds(connector, organization_id=None):
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _get_aws_creds as _f
    return await _f(connector, organization_id=organization_id)


def _make_ec2_client(creds):
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _make_ec2_client as _f
    return _f(creds)


async def _take_snapshot(asset_id: str, instance_id: str, connector, organization_id=None) -> dict:
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _take_snapshot as _f
    return await _f(asset_id, instance_id, connector, organization_id=organization_id)


async def _restore_snapshot(asset_id: str, snapshot_id: str, snapshot_meta: dict, connector, organization_id=None) -> dict:
    from app.connectors.executors.nexplane_agent._snapshot_helpers import _restore_snapshot as _f
    return await _f(asset_id, snapshot_id, snapshot_meta, connector, organization_id=organization_id)


async def _resolve_instance_id(asset_id: str) -> tuple:
    """Return (instance_id, organization_id) from asset metadata. Returns (None, None) on failure."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.asset import Asset
        import uuid as _uuid
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, _uuid.UUID(str(asset_id)))
            if asset:
                meta = asset.asset_metadata or {}
                org = str(asset.organization_id) if asset.organization_id else None
                return meta.get("instance_id"), org
    except Exception as e:
        logger.debug("Could not resolve instance_id from asset metadata: %s", e)
    return None, None


async def _wait_for_agent(asset_id: str, timeout_s: int = _REREGISTER_TIMEOUT_S) -> bool:
    """Poll until the asset's agent sends a heartbeat or timeout expires.

    Uses dispatch_agent_job health_check (mirrors _poll_agent_reconnect in windows_os_upgrade).
    Returns True if agent came back online, False on timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            await dispatch_agent_job(
                command="health_check",
                parameters={},
                asset_ids=[asset_id],
                timeout_seconds=20,
            )
            return True
        except Exception:
            await asyncio.sleep(_REREGISTER_POLL_S)
    return False


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------

def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "target_kernel":   p.get("target_kernel", ""),
        "skip_snapshot":   bool(p.get("skip_snapshot", False)),
        "dry_run":         bool(p.get("dry_run", False)),
        "organization_id": p.get("organization_id"),
    }


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)
    target_kernel = p["target_kernel"]
    skip_snapshot = p["skip_snapshot"]
    organization_id = p.get("organization_id")

    if not target_kernel:
        raise ValueError("target_kernel required (e.g. '6.1.0-21-generic' or '6.1.0-21.21.amzn2.x86_64')")

    # ------------------------------------------------------------------
    # Step 1: Preflight
    # ------------------------------------------------------------------
    logger.info("Kernel upgrade preflight: target=%s asset=%s", target_kernel, asset_id)
    pf = await dispatch_agent_job(
        command="preflight_kernel_upgrade",
        parameters={"target_kernel": target_kernel},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if pf.get("status") == "blocked":
        return {
            "status": "failed",
            "phase": "preflight",
            "reason": pf.get("reason"),
            "current_kernel": pf.get("current_kernel"),
            "target_kernel": target_kernel,
            "asset_id": asset_id,
        }
    for w in pf.get("warnings", []):
        logger.warning("Preflight warning: %s", w)

    # ------------------------------------------------------------------
    # Step 2: EBS snapshot (skip on bare-metal or when skip_snapshot=True)
    # ------------------------------------------------------------------
    snap_result = {}
    if not skip_snapshot:
        try:
            creds = await _get_aws_creds(connector, organization_id=organization_id)
            if creds and creds.get("access_key_id"):
                instance_id, resolved_org = await _resolve_instance_id(asset_id)
                if not organization_id and resolved_org:
                    organization_id = resolved_org

                if instance_id:
                    snap_result = await _take_snapshot(asset_id, instance_id, connector, organization_id)
                    logger.info("Pre-upgrade snapshot: %s for asset %s",
                                snap_result.get("snapshot_id"), asset_id)
                else:
                    logger.info("No EC2 instance_id found for asset %s — skipping EBS snapshot (bare-metal path)", asset_id)
            else:
                logger.info("No AWS credentials — skipping EBS snapshot (bare-metal path)")
        except Exception as e:
            logger.warning("Snapshot failed (non-blocking): %s", e)

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "preflight": pf,
            "snapshot_id": snap_result.get("snapshot_id"),
            "target_kernel": target_kernel,
            "asset_id": asset_id,
        }

    # ------------------------------------------------------------------
    # Step 3: Install kernel and arm grub-reboot (one-shot next-boot)
    # ------------------------------------------------------------------
    logger.info("Installing kernel %s on %s", target_kernel, asset_id)
    exec_result = await dispatch_agent_job(
        command="execute_kernel_upgrade",
        parameters={
            "target_kernel": target_kernel,
            "already_installed": pf.get("already_installed", False),
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    previous_kernel = exec_result.get("previous_kernel", pf.get("current_kernel", ""))

    # ------------------------------------------------------------------
    # Step 4: Trigger reboot (grub-reboot already armed as one-shot)
    # ------------------------------------------------------------------
    logger.info("Triggering reboot on %s", asset_id)
    await dispatch_agent_job(
        command="reboot",
        parameters={"graceful_delay_seconds": 60},
        asset_ids=[asset_id],
        timeout_seconds=90,
    )

    # ------------------------------------------------------------------
    # Step 5: Wait for agent re-registration (up to 30 minutes)
    # ------------------------------------------------------------------
    logger.info("Waiting for agent re-registration on %s (up to %ds)", asset_id, _REREGISTER_TIMEOUT_S)
    came_back = await _wait_for_agent(asset_id, timeout_s=_REREGISTER_TIMEOUT_S)
    if not came_back:
        return {
            "status": "failed",
            "phase": "reboot_wait",
            "reason": f"Agent did not re-register within {_REREGISTER_TIMEOUT_S}s after reboot",
            "target_kernel": target_kernel,
            "previous_kernel": previous_kernel,
            "snapshot_id": snap_result.get("snapshot_id"),
            "instance_id": snap_result.get("instance_id"),
            "root_volume_id": snap_result.get("root_volume_id"),
            "root_device_name": snap_result.get("root_device_name"),
            "availability_zone": snap_result.get("availability_zone"),
            "region": snap_result.get("region"),
            "asset_id": asset_id,
            "data_loss_warning": (
                "System did not come back after kernel upgrade reboot. "
                "If this is a cloud instance, restore the pre-upgrade EBS snapshot. "
                "If bare-metal, manually set old kernel as GRUB default and reboot."
            ),
        }

    # ------------------------------------------------------------------
    # Step 6: Verify running kernel
    # ------------------------------------------------------------------
    logger.info("Verifying kernel version on %s", asset_id)
    verify = await dispatch_agent_job(
        command="verify_kernel_upgrade",
        parameters={"target_kernel": target_kernel},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if not verify.get("verified"):
        logger.warning("Kernel verify failed on %s — running=%s want=%s — attempting rollback",
                       asset_id, verify.get("running_kernel"), target_kernel)
        rollback_result = await _do_rollback(
            asset_id=asset_id,
            previous_kernel=previous_kernel,
            snap_result=snap_result,
            connector=connector,
            organization_id=organization_id,
        )
        return {
            "status": "verify_failed",
            "phase": "verify_kernel",
            "running_kernel": verify.get("running_kernel"),
            "target_kernel": target_kernel,
            "previous_kernel": previous_kernel,
            "snapshot_id": snap_result.get("snapshot_id"),
            "rollback": rollback_result,
            "asset_id": asset_id,
        }

    # ------------------------------------------------------------------
    # Step 7: Verify application services and containers are healthy
    # First pass without restart; second pass with restart_failed=True
    # ------------------------------------------------------------------
    logger.info("Verifying application services on %s after kernel upgrade", asset_id)
    svc_check = await dispatch_agent_job(
        command="verify_services_post_kernel_upgrade",
        parameters={
            "expected_services":   pf.get("running_services", []),
            "expected_containers": pf.get("running_containers", []),
            "restart_failed":      False,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if not svc_check.get("services_healthy"):
        logger.warning("Services unhealthy after reboot on %s — attempting restart of failed units", asset_id)
        svc_check = await dispatch_agent_job(
            command="verify_services_post_kernel_upgrade",
            parameters={
                "expected_services":   pf.get("running_services", []),
                "expected_containers": pf.get("running_containers", []),
                "restart_failed":      True,
            },
            asset_ids=[asset_id],
            timeout_seconds=180,
        )
        if not svc_check.get("services_healthy"):
            logger.error(
                "Service health failed after kernel upgrade on %s — failed_services=%s failed_containers=%s — rolling back",
                asset_id, svc_check.get("failed_services"), svc_check.get("failed_containers"),
            )
            rollback_result = await _do_rollback(
                asset_id=asset_id,
                previous_kernel=previous_kernel,
                snap_result=snap_result,
                connector=connector,
                organization_id=organization_id,
            )
            return {
                "status": "service_health_failed",
                "phase": "verify_services",
                "running_kernel": verify.get("running_kernel"),
                "failed_services":   svc_check.get("failed_services"),
                "failed_containers": svc_check.get("failed_containers"),
                "previous_kernel": previous_kernel,
                "snapshot_id": snap_result.get("snapshot_id"),
                "rollback": rollback_result,
                "asset_id": asset_id,
            }

    return {
        "status": "completed",
        "new_kernel": verify.get("running_kernel"),
        "previous_kernel": previous_kernel,
        "target_kernel": target_kernel,
        "snapshot_id": snap_result.get("snapshot_id"),
        "instance_id": snap_result.get("instance_id"),
        "root_volume_id": snap_result.get("root_volume_id"),
        "root_device_name": snap_result.get("root_device_name"),
        "availability_zone": snap_result.get("availability_zone"),
        "region": snap_result.get("region"),
        "services_verified": svc_check.get("services_healthy"),
        "preflight_warnings": pf.get("warnings", []),
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal rollback helper
# ---------------------------------------------------------------------------

async def _do_rollback(asset_id, previous_kernel, snap_result, connector, organization_id):
    """EBS restore (cloud) or grub fallback (bare-metal)."""
    if snap_result.get("snapshot_id"):
        try:
            restore = await _restore_snapshot(
                asset_id,
                snap_result["snapshot_id"],
                snap_result,
                connector,
                organization_id,
            )
            return {"strategy": "ebs_restore", "rolled_back": True, **restore}
        except Exception as e:
            logger.error("EBS restore failed: %s — falling back to GRUB rollback", e)

    # Bare-metal / EBS restore failed — dispatch rollback_kernel_upgrade
    if not previous_kernel:
        return {"strategy": "none", "rolled_back": False, "reason": "No previous_kernel and no snapshot"}

    try:
        rb = await dispatch_agent_job(
            command="rollback_kernel_upgrade",
            parameters={"previous_kernel": previous_kernel},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        came_back = await _wait_for_agent(asset_id, timeout_s=_REREGISTER_TIMEOUT_S)
        return {
            "strategy": "grub_fallback",
            "rolled_back": rb.get("rolled_back", False) and came_back,
            "reboot_result": rb,
            "agent_recovered": came_back,
        }
    except Exception as e:
        return {"strategy": "grub_fallback", "rolled_back": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Rollback (operator-initiated)
# ---------------------------------------------------------------------------

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0] or ""
    )
    if not asset_id:
        return {
            "rolled_back": False,
            "reason": "No asset_id in execution_result — cannot determine target host",
            "data_loss_warning": (
                "Manual intervention required: identify the host and boot the previous kernel "
                "by editing GRUB or restoring the pre-upgrade EBS snapshot."
            ),
        }

    previous_kernel = execution_result.get("previous_kernel", "")
    organization_id = (parameters.get("desired_outcome") or parameters).get("organization_id")

    snap_result = {k: execution_result.get(k) for k in
                   ("snapshot_id", "instance_id", "root_volume_id",
                    "root_device_name", "availability_zone", "region")}

    result = await _do_rollback(
        asset_id=asset_id,
        previous_kernel=previous_kernel,
        snap_result=snap_result,
        connector=connector,
        organization_id=organization_id,
    )
    return {
        **result,
        "data_loss_warning": (
            "Kernel was reverted to the previous version. "
            "Any kernel-specific configuration or DKMS module changes made for the new kernel "
            "are still on disk but inactive. Verify system services after rollback."
        ),
    }
