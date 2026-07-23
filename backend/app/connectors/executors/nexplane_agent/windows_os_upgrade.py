# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Windows OS upgrade executor.

Upgrades Windows Server via the Nexplane agent (which runs natively on Windows).
The agent dispatches PowerShell commands to trigger Windows Setup/DISM upgrade.

Flow: preflight → EBS snapshot → trigger upgrade (DISM/Windows Update) →
      wait for reboot + agent re-registration (up to 30 min) → verify → done.

Rollback: restore EBS snapshot (EBS root volume swap, same as Linux os_upgrade).
The agent will be unavailable during the reboot window — the executor polls for
re-registration before declaring success.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Windows reboots during in-place upgrade can take up to 30 minutes
_REBOOT_POLL_TIMEOUT_SECONDS = 1800
_REBOOT_POLL_INTERVAL_SECONDS = 30


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    target_version = parameters.get("target_version", "")  # e.g. "2025" or "2022"
    upgrade_method = parameters.get("upgrade_method", "windows_update")  # windows_update | dism_iso
    iso_path = parameters.get("iso_path", "")  # required if upgrade_method == dism_iso
    dry_run = bool(parameters.get("dry_run", False))
    skip_snapshot = bool(parameters.get("skip_snapshot", False))

    if not target_version:
        raise ValueError("target_version required (e.g. '2025' for Windows Server 2025)")

    if upgrade_method == "dism_iso" and not iso_path:
        raise ValueError("iso_path required when upgrade_method is dism_iso")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight
    logger.info(f"Windows upgrade preflight for {asset_id}, target={target_version}")
    preflight = await dispatch_agent_job(
        command="preflight_windows_upgrade",
        parameters={
            "target_version": target_version,
            "upgrade_method": upgrade_method,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if preflight.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": preflight.get("reason", "Preflight checks failed"),
            "preflight": preflight,
        }

    if dry_run:
        return {
            "status": "dry_run",
            "current_version": preflight.get("current_version"),
            "target_version": target_version,
            "upgrade_method": upgrade_method,
            "estimated_duration_minutes": preflight.get("estimated_duration_minutes", 45),
            "warnings": preflight.get("warnings", []),
        }

    # Step 2: EBS snapshot before upgrade
    snapshot_meta = None
    if not skip_snapshot:
        snapshot_meta = await _maybe_take_snapshot(asset_id, connector)
        if snapshot_meta:
            logger.info(f"Pre-upgrade snapshot: {snapshot_meta.get('snapshot_id')}")
        else:
            logger.warning(f"Could not take pre-upgrade snapshot for {asset_id} — proceeding without")

    # Step 3: Trigger Windows upgrade (agent dispatches PowerShell; returns immediately as reboot imminent)
    logger.info(f"Triggering Windows upgrade on {asset_id} via {upgrade_method}")
    try:
        trigger_result = await dispatch_agent_job(
            command="execute_windows_upgrade",
            parameters={
                "target_version": target_version,
                "upgrade_method": upgrade_method,
                "iso_path": iso_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,  # just triggers the upgrade; reboot happens async
        )
    except Exception as exc:
        # Agent may disconnect before responding (reboot initiated) — treat disconnect as success
        if _is_agent_disconnect_error(exc):
            logger.info(f"Agent disconnected (expected — Windows reboot initiated) on {asset_id}")
            trigger_result = {"triggered": True, "note": "Agent disconnected (reboot expected)"}
        else:
            return {
                "status": "failed",
                "phase": "trigger",
                "error": str(exc),
                "snapshot_meta": snapshot_meta,
            }

    # Step 4: Poll for agent re-registration (agent comes back after upgrade + reboot)
    logger.info(f"Waiting up to {_REBOOT_POLL_TIMEOUT_SECONDS}s for Windows agent to re-register on {asset_id}")
    agent_recovered = await _poll_agent_health(asset_id, _REBOOT_POLL_TIMEOUT_SECONDS, _REBOOT_POLL_INTERVAL_SECONDS)

    if not agent_recovered:
        return {
            "status": "timed_out",
            "phase": "waiting_for_reboot",
            "note": "Agent did not re-register within 30 min — check instance manually",
            "snapshot_meta": snapshot_meta,
            "trigger_result": trigger_result,
        }

    # Step 5: Verify upgrade completed
    try:
        verify_result = await dispatch_agent_job(
            command="verify_windows_upgrade",
            parameters={"target_version": target_version},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
    except Exception as exc:
        verify_result = {"verified": False, "error": str(exc)}

    if not verify_result.get("verified", True):
        logger.warning(f"Windows upgrade verification failed on {asset_id}")
        if snapshot_meta and snapshot_meta.get("instance_id"):
            rollback_result = await _restore_snapshot_full(asset_id, snapshot_meta, connector)
            return {
                "status": "failed_and_rolled_back",
                "verify_result": verify_result,
                "snapshot_meta": snapshot_meta,
                "rollback_result": rollback_result,
            }

    return {
        "status": "completed",
        "previous_version": preflight.get("current_version"),
        "new_version": verify_result.get("actual_version", target_version),
        "snapshot_meta": snapshot_meta,
        "trigger_result": trigger_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_meta = execution_result.get("snapshot_meta") or {}
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""

    if not snapshot_meta.get("instance_id"):
        return {
            "rolled_back": False,
            "reason": "no_snapshot_meta — EBS restore not possible; restore manually",
            "snapshot_id": snapshot_meta.get("snapshot_id"),
        }

    try:
        result = await _restore_snapshot_full(asset_id, snapshot_meta, connector)
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}

    return {
        "rolled_back": result.get("restored", False),
        "snapshot_id": snapshot_meta.get("snapshot_id"),
        "new_volume_id": result.get("new_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
    }


async def _poll_agent_health(asset_id: str, timeout: int, interval: int) -> bool:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    deadline = asyncio.get_event_loop().time() + timeout
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
            await asyncio.sleep(interval)
    return False


async def _maybe_take_snapshot(asset_id: str, connector) -> dict | None:
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, uuid.UUID(asset_id))
            if not asset:
                return None
            meta = asset.asset_metadata or {}
            instance_id = meta.get("instance_id")
            if not instance_id:
                return None
    except Exception:
        return None
    try:
        from app.connectors.executors.nexplane_agent.os_upgrade import (
            _get_aws_creds,
            _take_snapshot,
        )
        return await _take_snapshot(asset_id, instance_id, connector)
    except Exception as exc:
        logger.warning(f"Snapshot failed (non-fatal): {exc}")
        return None


async def _restore_snapshot_full(asset_id: str, snapshot_meta: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.os_upgrade import _restore_snapshot
    return await _restore_snapshot(asset_id, snapshot_meta["snapshot_id"], snapshot_meta, connector)


def _is_agent_disconnect_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ("connection", "disconnect", "timeout", "reset", "eof", "closed"))
