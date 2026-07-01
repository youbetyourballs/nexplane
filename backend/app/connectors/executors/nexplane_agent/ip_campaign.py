# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
ip_campaign executor — fleet IP migration using migrate_ip in rolling batches.

Mirrors the run_patch_campaign.py pattern:
  - Accepts a migration_plan list of per-asset IP configurations
  - Batches assets, runs each batch in parallel via asyncio.gather()
  - Aborts if the error fraction exceeds abort_error_threshold
  - Completed batches are NOT automatically rolled back (fleet scale — operator recovers)
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Parameters:
        migration_plan          list  — [{asset_id, interface, new_ip_v4, new_gateway_v4, method}, ...]
        batch_size              int   — default 5
        batch_interval_seconds  int   — seconds to sleep between batches, default 60
        abort_error_threshold   float — fraction of failures to abort, default 0.1
        method                  str   — default method for all hosts ("auto")
        commit_timer_seconds    int   — default 30
        dry_run                 bool  — if True, plan without executing

    Returns:
        total_assets       int
        batches_completed  int
        hosts_migrated     list[str]   asset IDs successfully migrated
        hosts_failed       list[dict]  [{asset_id, error}]
        aborted            bool
        abort_reason       str | None
        dry_run            bool
    """

    migration_plan: list[dict] = parameters.get("migration_plan", [])
    batch_size = int(parameters.get("batch_size", 5))
    batch_interval_seconds = int(parameters.get("batch_interval_seconds", 60))
    abort_threshold = float(parameters.get("abort_error_threshold", 0.1))
    default_method = parameters.get("method", "auto")
    commit_timer_seconds = int(parameters.get("commit_timer_seconds", 30))
    dry_run = bool(parameters.get("dry_run", False))

    if not migration_plan:
        raise ValueError("migration_plan must be a non-empty list of per-asset configurations")

    if dry_run:
        return {
            "total_assets": len(migration_plan),
            "batches_completed": 0,
            "hosts_migrated": [],
            "hosts_failed": [],
            "aborted": False,
            "abort_reason": None,
            "dry_run": True,
            "plan_preview": migration_plan,
        }

    migrated: list[str] = []
    failed: list[dict] = []
    aborted = False
    abort_reason: str | None = None
    batch_num = 0

    batches = [migration_plan[i:i + batch_size] for i in range(0, len(migration_plan), batch_size)]

    for batch_num, batch in enumerate(batches):
        if batch_num > 0:
            await asyncio.sleep(batch_interval_seconds)

        results = await asyncio.gather(
            *[
                _migrate_single_host(
                    dispatch_agent_job=dispatch_agent_job,
                    host_plan=host_plan,
                    default_method=default_method,
                    commit_timer_seconds=commit_timer_seconds,
                )
                for host_plan in batch
            ],
            return_exceptions=True,
        )

        for host_plan, result in zip(batch, results):
            if isinstance(result, Exception):
                failed.append({
                    "asset_id": host_plan.get("asset_id", "unknown"),
                    "error": str(result),
                })
            else:
                migrated.append(host_plan["asset_id"])

        total_attempted = len(migrated) + len(failed)
        if total_attempted > 0 and len(failed) / total_attempted > abort_threshold:
            aborted = True
            abort_reason = (
                f"Error rate {len(failed)/total_attempted:.0%} exceeded threshold "
                f"{abort_threshold:.0%} after batch {batch_num + 1}"
            )
            break

    return {
        "total_assets": len(migration_plan),
        "batches_completed": batch_num + 1,
        "hosts_migrated": migrated,
        "hosts_failed": failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
        "dry_run": False,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Campaign-level rollback is not supported — individual migrate_ip CRs have their own rollback."""
    return {
        "rolled_back": False,
        "reason": (
            "ip_campaign rollback is not supported at the campaign level. "
            "Initiate per-host migrate_ip rollbacks individually."
        ),
    }


async def _migrate_single_host(
    dispatch_agent_job: Any,
    host_plan: dict,
    default_method: str,
    commit_timer_seconds: int,
) -> None:
    """
    Dispatches a change_ip command for a single host in the migration plan.
    Raises RuntimeError on failure so asyncio.gather() captures it as an exception.
    """
    asset_id = host_plan.get("asset_id")
    if not asset_id:
        raise ValueError("host_plan entry missing 'asset_id'")

    agent_params = {
        "interface": host_plan.get("interface", "eth0"),
        "mode": host_plan.get("mode", "static"),
        "new_ip_v4": host_plan.get("new_ip_v4"),
        "new_gateway_v4": host_plan.get("new_gateway_v4"),
        "method": host_plan.get("method", default_method),
        "commit_timer_seconds": commit_timer_seconds,
    }
    agent_params = {k: v for k, v in agent_params.items() if v is not None}

    result = await dispatch_agent_job(
        command="change_ip",
        parameters=agent_params,
        asset_ids=[asset_id],
        timeout_seconds=commit_timer_seconds + 120,
    )

    # Agent returns status="completed" on success; anything else is a failure
    status = result.get("status", "completed")
    if status == "failed":
        raise RuntimeError(result.get("error", "agent reported failure"))
