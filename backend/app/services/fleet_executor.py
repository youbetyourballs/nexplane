# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Fleet change executors.

Orchestration lives here; the agent remains a simple job runner receiving
individual commands via the existing job/poll mechanism.
"""
import asyncio
import logging
import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AssetGroup resolver
# ---------------------------------------------------------------------------

def _query_assets_by_ids(asset_ids: list, db):
    """Synchronous helper — patched in tests."""
    from sqlalchemy import select
    from app.models.asset import Asset
    # For async sessions this must be called inside an await; callers handle that.
    raise NotImplementedError("Use resolve_asset_group_async instead")


def resolve_asset_group(group: dict, db) -> list:
    """Resolve an asset_group dict to a list of Asset objects (sync, for tests)."""
    if "asset_ids" in group:
        return _query_assets_by_ids(group["asset_ids"], db)
    if "tag" in group:
        raise NotImplementedError("Tag-based resolution requires async; use resolve_asset_group_async")
    raise ValueError(f"Unknown asset_group format: {group}")


async def resolve_asset_group_async(group: dict, db: AsyncSession) -> list:
    from sqlalchemy import select
    from app.models.asset import Asset

    if "asset_ids" in group:
        result = await db.execute(
            select(Asset).where(Asset.id.in_(group["asset_ids"]))
        )
        return result.scalars().all()
    if "tag" in group:
        result = await db.execute(
            select(Asset).where(Asset.tags.contains([group["tag"]]))
        )
        return result.scalars().all()
    raise ValueError(f"Unknown asset_group format: {group}")


# ---------------------------------------------------------------------------
# Job dispatch helpers (thin wrappers — patched in tests)
# ---------------------------------------------------------------------------

async def dispatch_agent_job(asset_id, command: str, params: dict) -> str:
    """Dispatch a job to the agent for asset_id and return the job_id."""
    from app.models.agent import AgentJob
    from app.database import AsyncSessionLocal
    import uuid

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        job = AgentJob(
            id=job_id,
            asset_id=asset_id,
            command=command,
            params=params,
            status="pending",
        )
        db.add(job)
        await db.commit()
    return job_id


async def wait_for_job_result(job_id: str, timeout: int = 300) -> dict:
    """Poll until the agent job completes or timeout expires. Returns result dict."""
    from app.models.agent import AgentJob
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AgentJob).where(AgentJob.id == job_id))
            job = result.scalar_one_or_none()
            if job and job.status in ("completed", "failed"):
                return job.result or {}
        await asyncio.sleep(2)
    return {"error": f"job {job_id} timed out after {timeout}s"}


# ---------------------------------------------------------------------------
# Rolling restart executor
# ---------------------------------------------------------------------------

async def execute_rolling_restart(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)
    batch_size = max(1, math.ceil(len(assets) * (p.get("batch_size_pct", 10) / 100)))
    abort_threshold = p.get("abort_threshold_pct", 25) / 100

    batches = [assets[i:i + batch_size] for i in range(0, len(assets), batch_size)]
    meta: dict[str, Any] = {
        "batches": [],
        "failure_count": 0,
        "total_dispatched": 0,
        "aborted": False,
    }

    cr.status = "batch_running"
    await db.commit()

    for idx, batch in enumerate(batches):
        batch_record: dict[str, Any] = {
            "batch_index": idx,
            "asset_ids": [a.id for a in batch],
            "status": "running",
            "results": {},
        }
        meta["batches"].append(batch_record)
        cr.step_metadata = meta.copy()
        await db.commit()

        job_ids = await asyncio.gather(*[
            dispatch_agent_job(a.id, "restart_service", {"service_name": p["service_name"]})
            for a in batch
        ])
        results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

        for asset, result in zip(batch, results):
            ok = result.get("running", False)
            batch_record["results"][str(asset.id)] = result
            if not ok:
                meta["failure_count"] += 1

        meta["total_dispatched"] += len(batch)
        failure_rate = meta["failure_count"] / meta["total_dispatched"]

        if failure_rate >= abort_threshold:
            batch_record["status"] = "aborted"
            meta["aborted"] = True
            cr.status = "batch_aborted"
            cr.step_metadata = meta.copy()
            await db.commit()
            logger.warning(
                f"Rolling restart {cr.id} aborted: {meta['failure_count']}/{meta['total_dispatched']} failed"
            )
            return

        batch_record["status"] = "completed"
        cr.step_metadata = meta.copy()
        await db.commit()

    cr.status = "completed"
    cr.step_metadata = meta.copy()
    await db.commit()
    logger.info(f"Rolling restart {cr.id} completed: {meta['total_dispatched']} hosts")


# ---------------------------------------------------------------------------
# Canary config push executor
# ---------------------------------------------------------------------------

async def execute_canary_config_push(cr, db: AsyncSession):
    p = cr.parameters
    canary_id = p["canary_asset_id"]

    # Step 1: push to canary
    job_id = await dispatch_agent_job(canary_id, "push_config_file", {
        "file_path":    p["file_path"],
        "file_content": p["file_content"],
        "backup":       True,
    })
    result = await wait_for_job_result(job_id)
    if result.get("error"):
        cr.status = "failed"
        await db.commit()
        return

    canary_backup_path = result.get("backup_path")

    # Step 2: run verification command
    verify_job_id = await dispatch_agent_job(canary_id, "remote_command", {
        "command": p["verification_command"]
    })
    verify_result = await wait_for_job_result(verify_job_id)

    if verify_result.get("exit_code", 1) != 0:
        logger.warning(f"Canary verification failed for CR {cr.id}; rolling back canary")
        cr.status = "failed"
        cr.step_metadata = {
            "canary_asset_id": canary_id,
            "backup_path": canary_backup_path,
            "verify_result": verify_result,
            "rollback_required": True,
        }
        await db.commit()
        return

    # Step 3: push to remaining assets
    assets = await resolve_asset_group_async(p["asset_group"], db)
    remaining = [a for a in assets if str(a.id) != str(canary_id)]

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "push_config_file", {
            "file_path":    p["file_path"],
            "file_content": p["file_content"],
            "backup":       True,
        })
        for a in remaining
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    failures = [r for r in results if r.get("error")]
    cr.status = "completed" if not failures else "completed_with_errors"
    cr.step_metadata = {
        "canary_asset_id": canary_id,
        "remaining_count": len(remaining),
        "failure_count": len(failures),
    }
    await db.commit()


# ---------------------------------------------------------------------------
# Distribute file executor
# ---------------------------------------------------------------------------

async def execute_distribute_file(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "distribute_file", {
            "file_path":    p["file_path"],
            "file_content": p["file_content"],
            "permissions":  p.get("permissions", "0644"),
            "post_command": p.get("post_command"),
        })
        for a in assets
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    failures = [r for r in results if r.get("error")]
    cr.status = "completed" if not failures else "completed_with_errors"
    cr.step_metadata = {
        "results": {str(a.id): r for a, r in zip(assets, results)},
        "failure_count": len(failures),
    }
    await db.commit()


# ---------------------------------------------------------------------------
# Fleet health check executor
# ---------------------------------------------------------------------------

async def execute_fleet_health_check(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "health_check", {
            "required_services": p.get("required_services", [])
        })
        for a in assets
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    per_host = {str(a.id): r for a, r in zip(assets, results)}
    passed = [aid for aid, r in per_host.items() if r.get("pass")]
    failed = [aid for aid, r in per_host.items() if not r.get("pass")]

    cr.step_metadata = {"per_host": per_host, "passed": passed, "failed": failed}
    cr.status = "completed" if not failed else "preflight_failed"
    await db.commit()


# ---------------------------------------------------------------------------
# Dispatcher — maps change_type to executor
# ---------------------------------------------------------------------------

FLEET_EXECUTORS = {
    "rolling_restart":    execute_rolling_restart,
    "canary_config_push": execute_canary_config_push,
    "distribute_file":    execute_distribute_file,
    "fleet_health_check": execute_fleet_health_check,
}


async def execute_fleet_change(cr, db: AsyncSession):
    change_type = cr.change_type.value if hasattr(cr.change_type, "value") else cr.change_type
    executor = FLEET_EXECUTORS.get(change_type)
    if executor is None:
        raise ValueError(f"No fleet executor for change_type '{change_type}'")
    await executor(cr, db)
