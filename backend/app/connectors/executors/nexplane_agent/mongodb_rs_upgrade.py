# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
MongoDB replica set upgrade executor.
Upgrades via sequential FCV hops: secondaries first, then stepdown + upgrade primary.
setFeatureCompatibilityVersion is the point of no return.
Flow: preflight → mongodump snapshot → (per FCV hop: upgrade secondaries → stepdown → upgrade primary → set FCV) → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

# Sequential MongoDB version upgrade chain
_MONGO_VERSION_SEQUENCE = ["4.4", "5.0", "6.0", "7.0"]


def _compute_fcv_chain(source_version: str, target_version: str) -> list:
    seq = _MONGO_VERSION_SEQUENCE
    if source_version not in seq:
        raise ValueError(f"{source_version!r} not in supported MongoDB versions: {seq}")
    if target_version not in seq:
        raise ValueError(f"{target_version!r} not in supported MongoDB versions: {seq}")
    src_idx = seq.index(source_version)
    tgt_idx = seq.index(target_version)
    if tgt_idx <= src_idx:
        raise ValueError(f"target_version must be newer than source_version")
    return seq[src_idx: tgt_idx + 1]


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    rs_members = parameters.get("rs_members", [])
    admin_user = parameters.get("admin_user", "admin")
    admin_password = parameters.get("admin_password")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")
    if not rs_members:
        raise ValueError("rs_members required — list of {host, port, priority}")

    fcv_chain = _compute_fcv_chain(source_version, target_version)

    # Phase 1: Preflight
    preflight = await dispatch_agent_job(
        command="preflight_mongo_rs_upgrade",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "rs_members": rs_members,
            "fcv_chain": fcv_chain,
            "admin_user": admin_user,
            "admin_password": admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if dry_run:
        return {"status": "dry_run", "fcv_chain": fcv_chain, "preflight": preflight}

    # Phase 2: mongodump snapshot
    snapshot = await dispatch_agent_job(
        command="mongodump_snapshot",
        parameters={"rs_members": rs_members, "admin_user": admin_user, "admin_password": admin_password},
        asset_ids=[asset_id],
        timeout_seconds=3600,
    )
    dump_archive_path = snapshot.get("dump_archive_path")

    # Phase 3: Per-FCV-hop upgrade
    fcv_set = False
    completed_hops = []
    rs_members_upgraded = []

    for i in range(len(fcv_chain) - 1):
        from_ver = fcv_chain[i]
        to_ver = fcv_chain[i + 1]
        hop_label = f"{from_ver}→{to_ver}"
        logger.info(f"MongoDB RS upgrade hop: {hop_label}")

        # Upgrade secondaries first
        secondaries = [m for m in rs_members if m.get("priority", 1) < max(m2.get("priority", 1) for m2 in rs_members)]
        for sec in secondaries:
            await dispatch_agent_job(
                command="mongo_rs_upgrade_secondary",
                parameters={"member": sec, "target_version": to_ver, "admin_user": admin_user, "admin_password": admin_password},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            rs_members_upgraded.append(sec.get("host"))

        # Step down primary
        await dispatch_agent_job(
            command="mongo_rs_stepdown_primary",
            parameters={"rs_members": rs_members, "admin_user": admin_user, "admin_password": admin_password},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )

        # Upgrade old primary (now secondary)
        primary_member = max(rs_members, key=lambda m: m.get("priority", 1))
        await dispatch_agent_job(
            command="mongo_rs_upgrade_old_primary",
            parameters={"member": primary_member, "target_version": to_ver, "admin_user": admin_user, "admin_password": admin_password},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        rs_members_upgraded.append(primary_member.get("host"))

        # Wait for election
        await dispatch_agent_job(
            command="mongo_rs_wait_primary_election",
            parameters={"rs_members": rs_members, "admin_user": admin_user, "admin_password": admin_password},
            asset_ids=[asset_id],
            timeout_seconds=180,
        )

        # Set FCV — POINT OF NO RETURN
        logger.warning(f"Setting MongoDB FCV to {to_ver} — this is irreversible")
        await dispatch_agent_job(
            command="mongo_set_fcv",
            parameters={"fcv_version": to_ver, "rs_members": rs_members, "admin_user": admin_user, "admin_password": admin_password},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        fcv_set = True
        completed_hops.append(hop_label)
        logger.info(f"FCV hop {hop_label} complete")

    # Phase 4: Verify
    verify = await dispatch_agent_job(
        command="verify_mongo_rs",
        parameters={"target_version": target_version, "rs_members": rs_members, "admin_user": admin_user, "admin_password": admin_password},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("fcv_matches") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "fcv_chain": fcv_chain,
        "completed_hops": completed_hops,
        "rs_members_upgraded": list(set(rs_members_upgraded)),
        "fcv_set": fcv_set,
        "dump_archive_path": dump_archive_path,
        "primary_host": verify.get("primary_host"),
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    fcv_set = execution_result.get("fcv_set", False)
    dump_archive_path = execution_result.get("dump_archive_path")

    if fcv_set:
        return {
            "rolled_back": False,
            "reason": "setFeatureCompatibilityVersion already executed — MongoDB FCV is irreversible",
            "manual_steps": [
                "1. Restore from mongodump archive if data rollback is needed",
                f"2. Archive path: {dump_archive_path}",
                "3. Reinstall old MongoDB binaries and restore dump",
            ],
            "dump_archive_path": dump_archive_path,
        }

    # Before FCV set: restore dump + reinstall old binaries
    if not dump_archive_path:
        return {"rolled_back": False, "reason": "no dump_archive_path — cannot restore"}

    p = parameters.get("desired_outcome") or parameters
    result = await dispatch_agent_job(
        command="mongo_rs_rollback_from_dump",
        parameters={
            "dump_archive_path": dump_archive_path,
            "source_version": execution_result.get("source_version"),
            "rs_members": p.get("rs_members", []),
            "admin_user": p.get("admin_user", "admin"),
            "admin_password": p.get("admin_password"),
        },
        asset_ids=[asset_id],
        timeout_seconds=7200,
    )

    return {
        "rolled_back": result.get("success", False),
        "strategy": "mongodump_restore",
        "dump_archive_path": dump_archive_path,
        "agent_result": result,
    }
