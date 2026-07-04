# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""WebSocket log-tail endpoint for change-request execution streams."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.services.auth_service import get_current_user

router = APIRouter(tags=["Logs"])

_TERMINAL_CR_STATUSES = {
    ChangeRequestStatus.completed,
    ChangeRequestStatus.failed,
    ChangeRequestStatus.rolled_back,
    ChangeRequestStatus.rollback_partial,
    ChangeRequestStatus.rollback_failed,
    ChangeRequestStatus.manual_recovery_required,
    ChangeRequestStatus.rejected,
    ChangeRequestStatus.batch_aborted,
    ChangeRequestStatus.completed_with_errors,
    ChangeRequestStatus.preflight_failed,
}

_POLL_INTERVAL = 0.5  # seconds


@router.websocket("/change-requests/{cr_id}/log-tail")
async def cr_log_tail(websocket: WebSocket, cr_id: uuid.UUID, token: str = "") -> None:
    """Stream execution log lines for a change request in real time.

    Authenticates via query-param ``token=<jwt>``.  Sends JSON-line events:
    - ``{"event": "log", "step": N, "action_id": "...", "ts": "...", "message": "..."}``
    - ``{"event": "done", "status": "<status>"}`` on terminal state, then closes.
    """
    await websocket.accept()

    # --- Auth ---
    if not token:
        await websocket.send_text(json.dumps({"event": "error", "message": "missing token"}))
        await websocket.close(code=4001)
        return

    async with AsyncSessionLocal() as db:
        user = await get_current_user(db, token)

    if not user:
        await websocket.send_text(json.dumps({"event": "error", "message": "unauthorized"}))
        await websocket.close(code=4003)
        return

    try:
        seen_step_keys: set[str] = set()
        last_result_snapshot: dict[str, Any] = {}

        while True:
            async with AsyncSessionLocal() as db:
                cr_result = await db.execute(
                    select(ChangeRequest).where(
                        ChangeRequest.id == cr_id,
                        ChangeRequest.organization_id == user.organization_id,
                    )
                )
                cr = cr_result.scalar_one_or_none()

                if cr is None:
                    await websocket.send_text(json.dumps({"event": "error", "message": "not found"}))
                    await websocket.close(code=4004)
                    return

                # Get latest execution run
                run_result = await db.execute(
                    select(ExecutionRun)
                    .where(ExecutionRun.change_request_id == cr_id)
                    .order_by(ExecutionRun.started_at.desc())
                    .limit(1)
                )
                run = run_result.scalar_one_or_none()

            if run is not None:
                result: dict[str, Any] = run.result or {}
                new_events = _extract_new_log_events(result, last_result_snapshot, seen_step_keys)
                for event in new_events:
                    await websocket.send_text(json.dumps(event))
                last_result_snapshot = result

            # Check terminal state
            cr_status = cr.status if cr else None
            if cr_status in _TERMINAL_CR_STATUSES:
                await websocket.send_text(
                    json.dumps({"event": "done", "status": str(cr_status.value if hasattr(cr_status, "value") else cr_status)})
                )
                await websocket.close()
                return

            await asyncio.sleep(_POLL_INTERVAL)

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def _extract_new_log_events(
    result: dict[str, Any],
    prev_result: dict[str, Any],
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    """Diff result JSON to emit new step log lines."""
    events: list[dict[str, Any]] = []

    # step_results: {step_key: {status, started_at, completed_at, output, error, ...}}
    step_results: dict[str, Any] = result.get("step_results", {})
    prev_steps: dict[str, Any] = prev_result.get("step_results", {})

    for step_key, step_data in step_results.items():
        if not isinstance(step_data, dict):
            continue

        prev_step = prev_steps.get(step_key, {})

        # Emit a log line when we see a new step or a status change
        status = step_data.get("status", "")
        prev_status = prev_step.get("status", "")

        cache_key = f"{step_key}:{status}"
        if cache_key in seen_keys:
            continue

        seen_keys.add(cache_key)

        # Derive a human-readable message
        if status in ("running", "in_progress"):
            message = f"Step {step_key}: started"
        elif status in ("completed", "success", "done"):
            message = f"Step {step_key}: completed"
            output = step_data.get("output") or step_data.get("result")
            if output and isinstance(output, str):
                message = f"Step {step_key}: {output[:200]}"
        elif status in ("failed", "error"):
            err = step_data.get("error") or step_data.get("message", "")
            message = f"Step {step_key}: FAILED — {err}"
        else:
            if not status:
                continue
            message = f"Step {step_key}: {status}"

        ts = (
            step_data.get("started_at")
            or step_data.get("completed_at")
            or datetime.now(timezone.utc).isoformat()
        )

        # Try to extract a step number from key (e.g. "step_1_create_user" → 1)
        parts = step_key.split("_")
        step_n: int | None = None
        for p in parts:
            if p.isdigit():
                step_n = int(p)
                break

        events.append({
            "event": "log",
            "step": step_n,
            "action_id": step_key,
            "ts": ts,
            "message": message,
        })

    # Also surface top-level log entries if the executor writes them
    log_lines: list[Any] = result.get("log", [])
    prev_log_count = len(prev_result.get("log", []))
    for i, line in enumerate(log_lines[prev_log_count:], start=prev_log_count):
        cache_key = f"log:{i}"
        if cache_key in seen_keys:
            continue
        seen_keys.add(cache_key)
        if isinstance(line, str):
            events.append({
                "event": "log",
                "step": None,
                "action_id": None,
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": line,
            })
        elif isinstance(line, dict):
            events.append({
                "event": "log",
                "step": line.get("step"),
                "action_id": line.get("action_id"),
                "ts": line.get("ts", datetime.now(timezone.utc).isoformat()),
                "message": line.get("message", str(line)),
            })

    return events
