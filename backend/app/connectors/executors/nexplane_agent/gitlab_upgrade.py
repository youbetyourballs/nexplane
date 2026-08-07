# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GitLab major version upgrade executor.

GitLab cannot skip major versions. The executor computes the required hop chain:
  - Within same major: direct upgrade (15.4→15.11 is fine)
  - Crossing major boundary: must hit <major>.11 (last minor) before next major
  - Example: 15.11→17.2 → hops: 15.11→16.0→16.11→17.0→17.2

Per hop: backup → install → reconfigure → wait background migrations → verify.
ROLLBACK_CAPABILITY = "full" — background migrations are irreversible once run.
Rollback restores from the backup taken at the START of the current hop.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# GitLab last minor version per major (kept current to 17.x as of 2026)
_GITLAB_LAST_MINOR = {
    14: 10,
    15: 11,
    16: 11,
    17: 11,
}


def _parse_version(version_str: str) -> tuple:
    """Parse 'MAJOR.MINOR' or 'MAJOR.MINOR.PATCH' → (major, minor, patch)."""
    parts = str(version_str).split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse GitLab version: {version_str!r}")
    return (major, minor, patch)


def _fmt(major: int, minor: int) -> str:
    return f"{major}.{minor}"


def compute_gitlab_hop_chain(source_version: str, target_version: str) -> list:
    """Compute the ordered list of GitLab version hops required to reach target.

    Rules:
      1. Cannot skip major versions.
      2. Before crossing from major N to major N+1, must be at <N>.11
         (or whatever _GITLAB_LAST_MINOR[N] is).
      3. Within the same major, direct upgrade is safe.
      4. The chain includes source_version and target_version as endpoints.

    Examples:
      15.11 → 16.0  : ["15.11", "16.0"]          (one hop, already at last minor)
      15.4  → 16.0  : ["15.4", "15.11", "16.0"]  (must hit 15.11 first)
      15.11 → 17.2  : ["15.11", "16.0", "16.11", "17.0", "17.2"]
    """
    src_major, src_minor, _ = _parse_version(source_version)
    tgt_major, tgt_minor, _ = _parse_version(target_version)

    if (tgt_major, tgt_minor) <= (src_major, src_minor):
        raise ValueError(
            f"target_version {target_version!r} must be newer than source_version {source_version!r}"
        )

    hops = [source_version]

    current_major = src_major
    current_minor = src_minor

    while current_major < tgt_major:
        last_minor = _GITLAB_LAST_MINOR.get(current_major)
        if last_minor is None:
            raise ValueError(
                f"Unknown GitLab last minor for major {current_major}. "
                f"Update _GITLAB_LAST_MINOR in gitlab_upgrade.py."
            )

        # If not yet at last minor of current major, must upgrade there first
        if current_minor < last_minor:
            hop = _fmt(current_major, last_minor)
            hops.append(hop)
            current_minor = last_minor

        # Cross to next major at minor 0
        next_major = current_major + 1
        hop = _fmt(next_major, 0)
        hops.append(hop)
        current_major = next_major
        current_minor = 0

    # Now at target major — if not yet at target minor, add target
    if current_minor < tgt_minor:
        hops.append(_fmt(tgt_major, tgt_minor))

    # Deduplicate while preserving order (source may equal first waypoint)
    seen = set()
    deduped = []
    for h in hops:
        if h not in seen:
            seen.add(h)
            deduped.append(h)

    return deduped


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    gitlab_host = parameters.get("gitlab_host", "localhost")
    gitlab_admin_token = parameters.get("gitlab_admin_token", "")
    gitlab_package_type = parameters.get("gitlab_package_type", "omnibus")
    backup_s3_bucket = parameters.get("backup_s3_bucket")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '15.11')")
    if not target_version:
        raise ValueError("target_version required (e.g. '17.2')")
    if not gitlab_admin_token:
        raise ValueError("gitlab_admin_token required")

    # Compute hop chain — validate immediately so bad inputs fail before any infra ops
    try:
        hop_chain = compute_gitlab_hop_chain(source_version, target_version)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc)}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight
    logger.info(f"GitLab upgrade preflight {source_version}→{target_version}, hops={hop_chain}")
    preflight = await dispatch_agent_job(
        command="gitlab_preflight",
        parameters={
            "gitlab_host": gitlab_host,
            "gitlab_admin_token": gitlab_admin_token,
            "source_version": source_version,
            "hop_chain": hop_chain,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "hop_chain": hop_chain,
            "preflight": preflight,
        }

    # Steps 2+: Execute each hop
    hops_completed = []
    backup_paths = []

    for i in range(len(hop_chain) - 1):
        from_ver = hop_chain[i]
        to_ver = hop_chain[i + 1]
        hop_label = f"{from_ver}→{to_ver}"
        logger.info(f"GitLab hop: {hop_label}")

        # 2a: Backup before each hop
        backup_result = await dispatch_agent_job(
            command="gitlab_backup",
            parameters={
                "gitlab_host": gitlab_host,
                "backup_s3_bucket": backup_s3_bucket,
            },
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        backup_path = backup_result.get("backup_path", f"gitlab-backup-hop{i}")
        backup_paths.append({"hop": hop_label, "backup_path": backup_path})

        # 2b: Upgrade to hop version
        upgrade_result = await dispatch_agent_job(
            command="gitlab_upgrade_hop",
            parameters={
                "to_version": to_ver,
                "package_type": gitlab_package_type,
            },
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        if not upgrade_result.get("success", True):
            return {
                "status": "failed",
                "phase": f"upgrade_hop_{hop_label}",
                "error": upgrade_result.get("error"),
                "hops_completed": hops_completed,
                "backup_paths": backup_paths,
                "note": "Rollback will restore from most recent hop backup",
            }

        # 2c: Wait for background migrations
        await dispatch_agent_job(
            command="gitlab_wait_migrations",
            parameters={
                "gitlab_host": gitlab_host,
                "gitlab_admin_token": gitlab_admin_token,
                "timeout_seconds": 1800,
            },
            asset_ids=[asset_id],
            timeout_seconds=2100,  # 35 min hard stop (30 min poll + 5 min buffer)
        )

        # 2d: Verify hop version
        verify_hop = await dispatch_agent_job(
            command="gitlab_verify_version",
            parameters={
                "expected_version": to_ver,
                "gitlab_host": gitlab_host,
                "gitlab_admin_token": gitlab_admin_token,
            },
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        if not verify_hop.get("version_ok", True):
            return {
                "status": "failed",
                "phase": f"verify_hop_{hop_label}",
                "error": f"Version mismatch after hop: expected {to_ver}, got {verify_hop.get('actual_version')}",
                "hops_completed": hops_completed,
                "backup_paths": backup_paths,
            }

        hops_completed.append(hop_label)
        logger.info(f"GitLab hop {hop_label} completed")

    # Final verify
    final_verify = await dispatch_agent_job(
        command="gitlab_final_verify",
        parameters={
            "target_version": target_version,
            "gitlab_host": gitlab_host,
            "gitlab_admin_token": gitlab_admin_token,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "final_version": final_verify.get("actual_version", target_version),
        "hop_chain": hop_chain,
        "hops_completed": hops_completed,
        "backup_paths": backup_paths,
        "final_verify": final_verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore from the most recent per-hop backup. Background migrations are irreversible."""
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_paths = execution_result.get("backup_paths", [])

    if not backup_paths:
        return {
            "rolled_back": False,
            "reason": "No per-hop backups recorded in execution_result",
        }

    # Most recent backup = last entry (FILO)
    most_recent = backup_paths[-1]
    backup_path = most_recent.get("backup_path")
    hop = most_recent.get("hop", "unknown")

    if not backup_path:
        return {"rolled_back": False, "reason": "backup_path missing from most recent hop backup"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    restore_result = await dispatch_agent_job(
        command="gitlab_restore_backup",
        parameters={
            "backup_path": backup_path,
            "gitlab_host": parameters.get("gitlab_host", "localhost"),
            "backup_s3_bucket": parameters.get("backup_s3_bucket"),
        },
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    return {
        "rolled_back": restore_result.get("success", False),
        "restored_from_hop": hop,
        "backup_path": backup_path,
        "note": (
            "GitLab background migrations that already ran are irreversible. "
            f"Restored from backup taken before hop '{hop}'. "
            "Operator must re-run from this restore point."
        ),
        "restore_result": restore_result,
    }
