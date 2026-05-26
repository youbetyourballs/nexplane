"""
Nexplane MCP tools — Runbooks domain (4 tools).
"""
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_runbooks(
    token: str,
    tag: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List runbooks for the org with auto_execute setting and description.
    Optionally filter by tag. Returns summary fields — use get_runbook for full detail.
    """
    from sqlalchemy import select
    from app.models.runbook import Runbook

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(Runbook).where(
            Runbook.organization_id == user.organization_id
        ).order_by(Runbook.name).limit(limit)

        result = await db.execute(stmt)
        runbooks = result.scalars().all()

        filtered = runbooks
        if tag:
            filtered = [r for r in runbooks if tag in (r.tags or [])]

        return [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "version": r.version,
                "tags": r.tags,
                "auto_execute": r.auto_execute,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in filtered
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_runbook(token: str, runbook_id: str) -> dict[str, Any]:
    """
    Get runbook detail including all steps, types, and auto_execute setting.
    Step types include: cr (creates a CR), approval (human gate), condition, notification.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.runbook import Runbook

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Runbook).where(
                Runbook.id == _uuid.UUID(runbook_id),
                Runbook.organization_id == user.organization_id,
            ).options(selectinload(Runbook.steps))
        )
        r = result.scalar_one_or_none()
        if r is None:
            return {"error": "Runbook not found"}

        steps = [
            {
                "step_number": s.step_number,
                "name": s.name,
                "type": s.type,
                "change_type": s.change_type,
                "asset_selector": s.asset_selector,
                "on_failure": s.on_failure,
            }
            for s in sorted(r.steps or [], key=lambda s: s.step_number)
        ]

        return {
            "id": str(r.id),
            "name": r.name,
            "description": r.description,
            "version": r.version,
            "tags": r.tags,
            "auto_execute": r.auto_execute,
            "steps": steps,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def execute_runbook(
    token: str,
    runbook_id: str,
    target_asset_ids: list,
    context: dict = None,
) -> dict[str, Any]:
    """
    Execute a runbook against specified target assets. Creates a RunbookExecution record and
    queues execution. Returns execution ID and asset context bundle for the first target asset
    so you can validate the plan is appropriate. Execution is asynchronous — poll
    get_runbook_execution_status for progress.
    """
    from sqlalchemy import select
    from app.models.runbook import Runbook, RunbookExecution
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Runbook).where(
                Runbook.id == _uuid.UUID(runbook_id),
                Runbook.organization_id == user.organization_id,
            )
        )
        r = result.scalar_one_or_none()
        if r is None:
            return {"error": "Runbook not found"}

        execution = RunbookExecution(
            runbook_id=r.id,
            runbook_version=r.version,
            runbook_snapshot={"name": r.name, "steps": len(r.steps or [])},
            triggered_by=user.id,
            context={"target_asset_ids": target_asset_ids, **(context or {})},
            status="running",
            current_step=1,
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)

        asset_ctx = {}
        if target_asset_ids:
            try:
                asset_ctx = await build_asset_context(_uuid.UUID(target_asset_ids[0]), db)
            except Exception:
                pass

        return {
            "execution_id": str(execution.id),
            "runbook_id": str(r.id),
            "runbook_name": r.name,
            "status": execution.status,
            "target_asset_ids": target_asset_ids,
            "asset_context": asset_ctx,
            "message": "Execution started; poll get_runbook_execution_status for progress",
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_runbook_execution_status(
    token: str,
    execution_id: str,
) -> dict[str, Any]:
    """
    Get the current status of a runbook execution including per-step results.
    Status values: running / waiting_human / completed / failed / rolled_back.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.runbook import RunbookExecution

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(RunbookExecution).where(
                RunbookExecution.id == _uuid.UUID(execution_id),
            ).options(selectinload(RunbookExecution.step_results))
        )
        ex = result.scalar_one_or_none()
        if ex is None:
            return {"error": "Execution not found"}

        # Verify org scoping via runbook relationship
        rb_result = await db.execute(
            select(RunbookExecution.runbook_id).where(RunbookExecution.id == ex.id)
        )
        from app.models.runbook import Runbook
        rb_check = await db.execute(
            select(Runbook).where(
                Runbook.id == ex.runbook_id,
                Runbook.organization_id == user.organization_id,
            )
        )
        if rb_check.scalar_one_or_none() is None:
            return {"error": "Execution not found"}

        step_results = [
            {
                "step_number": sr.step_number,
                "step_name": sr.step_name,
                "step_type": sr.step_type,
                "status": sr.status,
                "started_at": sr.started_at.isoformat() if sr.started_at else None,
                "completed_at": sr.completed_at.isoformat() if sr.completed_at else None,
            }
            for sr in (ex.step_results or [])
        ]

        return {
            "execution_id": str(ex.id),
            "runbook_id": str(ex.runbook_id),
            "status": ex.status,
            "current_step": ex.current_step,
            "triggered_at": ex.triggered_at.isoformat() if ex.triggered_at else None,
            "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
            "step_results": step_results,
        }
    finally:
        await db_cm.__aexit__(None, None, None)
