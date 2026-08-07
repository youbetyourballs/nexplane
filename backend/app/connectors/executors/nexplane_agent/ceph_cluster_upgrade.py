# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Ceph cluster upgrade executor.
Strict upgrade order: MGR → MON → OSD → (RGW/MDS if present).
Sets noout before upgrade, unsets after. Mixed versions tolerated within compatibility window.
Downgrade not supported by Ceph — ROLLBACK_CAPABILITY is partial (surface daemon state).
Flow: preflight → set noout → upgrade MGRs → MONs → OSDs → (RGW/MDS) → unset noout → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    mgr_hosts = parameters.get("mgr_hosts", [])
    mon_hosts = parameters.get("mon_hosts", [])
    osd_hosts = parameters.get("osd_hosts", [])
    rgw_hosts = parameters.get("rgw_hosts", [])
    mds_hosts = parameters.get("mds_hosts", [])
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")
    if not mgr_hosts or not mon_hosts or not osd_hosts:
        raise ValueError("mgr_hosts, mon_hosts, and osd_hosts are all required")

    ceph_params = {
        "source_version": source_version,
        "target_version": target_version,
        "mgr_hosts": mgr_hosts,
        "mon_hosts": mon_hosts,
        "osd_hosts": osd_hosts,
        "rgw_hosts": rgw_hosts,
        "mds_hosts": mds_hosts,
    }

    # Phase 1: Preflight
    preflight = await dispatch_agent_job(
        command="preflight_ceph_upgrade",
        parameters=ceph_params,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if dry_run:
        return {"status": "dry_run", "source_version": source_version, "target_version": target_version, "preflight": preflight}

    # Phase 2: Set noout (prevent rebalancing during upgrade)
    await dispatch_agent_job(
        command="ceph_set_noout",
        parameters={"mgr_hosts": mgr_hosts},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    daemons_upgraded = []
    noout_unset = False

    try:
        # Phase 3: Upgrade MGRs
        for host in mgr_hosts:
            logger.info(f"Upgrading Ceph MGR on {host['host']}")
            await dispatch_agent_job(
                command="ceph_upgrade_mgr",
                parameters={"host": host, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            daemons_upgraded.append(f"mgr:{host['host']}")

        # Phase 4: Upgrade MONs
        for host in mon_hosts:
            logger.info(f"Upgrading Ceph MON on {host['host']}")
            await dispatch_agent_job(
                command="ceph_upgrade_mon",
                parameters={"host": host, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            await dispatch_agent_job(
                command="ceph_wait_quorum",
                parameters={"mon_hosts": mon_hosts},
                asset_ids=[asset_id],
                timeout_seconds=180,
            )
            daemons_upgraded.append(f"mon:{host['host']}")

        # Phase 5: Upgrade OSDs
        for host in osd_hosts:
            logger.info(f"Upgrading Ceph OSDs on {host['host']}")
            await dispatch_agent_job(
                command="ceph_upgrade_osd",
                parameters={"host": host, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=1200,
            )
            daemons_upgraded.append(f"osd:{host['host']}")

        # Phase 6: Upgrade RGW (optional)
        for host in rgw_hosts:
            logger.info(f"Upgrading Ceph RGW on {host['host']}")
            await dispatch_agent_job(
                command="ceph_upgrade_rgw",
                parameters={"host": host, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            daemons_upgraded.append(f"rgw:{host['host']}")

        # Phase 7: Upgrade MDS (optional)
        for host in mds_hosts:
            logger.info(f"Upgrading Ceph MDS on {host['host']}")
            await dispatch_agent_job(
                command="ceph_upgrade_mds",
                parameters={"host": host, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            daemons_upgraded.append(f"mds:{host['host']}")

    finally:
        # Phase 8: Unset noout (always, even on partial failure)
        try:
            await dispatch_agent_job(
                command="ceph_unset_noout",
                parameters={"mgr_hosts": mgr_hosts},
                asset_ids=[asset_id],
                timeout_seconds=60,
            )
            noout_unset = True
        except Exception as exc:
            logger.error(f"Failed to unset noout: {exc}. Manual intervention required: ceph osd unset noout")

    # Phase 9: Verify
    verify = await dispatch_agent_job(
        command="ceph_verify_health",
        parameters=ceph_params,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )

    return {
        "status": "completed" if verify.get("health") == "HEALTH_OK" else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "daemons_upgraded": daemons_upgraded,
        "noout_unset": noout_unset,
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Ceph does not support downgrade. Surface daemon state for operator."""
    daemons_upgraded = execution_result.get("daemons_upgraded", [])
    source_version = execution_result.get("source_version")
    target_version = execution_result.get("target_version")

    return {
        "rolled_back": False,
        "strategy": "partial_rollback",
        "reason": "Ceph does not support version downgrade. Mixed-version clusters are tolerated within the compatibility window.",
        "daemons_on_target_version": daemons_upgraded,
        "source_version": source_version,
        "target_version": target_version,
        "manual_steps": [
            "1. Check cluster health: ceph -s",
            "2. If cluster is unhealthy, restore from RBD snapshots or CephFS snapshots",
            "3. For partial upgrades within compatibility window, continue upgrading remaining daemons",
            "4. Contact Ceph support if cluster is in HEALTH_ERR state",
        ],
    }
