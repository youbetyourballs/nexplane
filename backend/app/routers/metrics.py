# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Live platform metrics endpoint (no auth required — platform-internal use)."""

import statistics
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("/live")
async def live_metrics() -> dict[str, Any]:
    """Return real-time platform health metrics.

    No authentication required — intended for internal monitoring use.
    """
    async with AsyncSessionLocal() as db:
        # active_crs: executing or rolling_back
        active_result = await db.execute(
            select(func.count()).where(
                ChangeRequest.status.in_([
                    ChangeRequestStatus.executing,
                    ChangeRequestStatus.batch_running,
                    ChangeRequestStatus.preflight_running,
                ])
            )
        )
        active_crs: int = active_result.scalar_one() or 0

        # queued_crs: approved but not yet executing
        queued_result = await db.execute(
            select(func.count()).where(ChangeRequest.status == ChangeRequestStatus.approved)
        )
        queued_crs: int = queued_result.scalar_one() or 0

        # running_steps: sum of in-progress steps across all active execution runs
        runs_result = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.status == ExecutionStatus.running
            )
        )
        active_runs = runs_result.scalars().all()

        running_steps = 0
        for run in active_runs:
            result = run.result or {}
            step_results = result.get("step_results", {})
            running_steps += sum(
                1
                for v in step_results.values()
                if isinstance(v, dict) and v.get("status") in ("running", "in_progress")
            )

        # Step latency from last 100 completed runs
        completed_runs_result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.status == ExecutionStatus.completed)
            .order_by(ExecutionRun.completed_at.desc())
            .limit(100)
        )
        completed_runs = completed_runs_result.scalars().all()

    latencies_ms = _extract_step_latencies(completed_runs)

    p50: float | None = None
    p95: float | None = None
    if latencies_ms:
        sorted_lat = sorted(latencies_ms)
        p50 = _percentile(sorted_lat, 50)
        p95 = _percentile(sorted_lat, 95)

    # Tunnel stats
    tunnel_agents_online = 0
    tunnel_active_streams = 0
    try:
        from app.tunnel.manager import get_manager
        stats = get_manager().agent_stats()
        tunnel_agents_online = sum(1 for v in stats.values() if v.get("online"))
        tunnel_active_streams = sum(v.get("active_streams", 0) for v in stats.values())
    except Exception:
        pass

    return {
        "active_crs": active_crs,
        "queued_crs": queued_crs,
        "running_steps": running_steps,
        "recent_step_latency_p50_ms": round(p50) if p50 is not None else None,
        "recent_step_latency_p95_ms": round(p95) if p95 is not None else None,
        "tunnel_agents_online": tunnel_agents_online,
        "tunnel_active_streams": tunnel_active_streams,
    }


def _extract_step_latencies(runs: list[ExecutionRun]) -> list[float]:
    """Extract per-step duration in ms from execution run result JSON."""
    latencies: list[float] = []
    for run in runs:
        result = run.result or {}
        step_results = result.get("step_results", {})
        for step_data in step_results.values():
            if not isinstance(step_data, dict):
                continue
            started = step_data.get("started_at")
            completed = step_data.get("completed_at")
            if started and completed:
                try:
                    from datetime import datetime
                    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in str(started) else "%Y-%m-%dT%H:%M:%S"
                    s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                    c = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                    diff_ms = (c - s).total_seconds() * 1000
                    if diff_ms >= 0:
                        latencies.append(diff_ms)
                except Exception:
                    pass
    return latencies


def _percentile(sorted_data: list[float], pct: int) -> float:
    """Compute a percentile from a sorted list."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
