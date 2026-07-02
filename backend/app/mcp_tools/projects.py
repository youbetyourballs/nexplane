# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Projects domain (15 tools).

Provides full project lifecycle: read, create, chat, CR assembly,
phase execution, rollback, and success criteria management.
"""
import logging
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        principal = user if user is not None else agent_token
        return principal, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


# ── TOOL 1: list_projects ────────────────────────────────────────────────────

@mcp.tool()
async def list_projects(
    token: str,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List all projects for the organisation. Optionally filter by status
    (draft/in_progress/completed/cancelled/rolling_back). Returns summary fields.
    Use get_project for full detail including CR list.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectStatus, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Project)
            .where(Project.organization_id == user.organization_id)
            .order_by(Project.created_at.desc())
            .limit(limit)
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
            )
        )
        if status:
            try:
                status_enum = ProjectStatus(status)
                stmt = stmt.where(Project.status == status_enum)
            except ValueError:
                return [{"error": f"Invalid status value: {status}. Valid values: {[s.value for s in ProjectStatus]}"}]

        result = await db.execute(stmt)
        projects = result.scalars().all()

        output = []
        for p in projects:
            cr_count = len(p.members) if p.members is not None else 0
            completed_count = 0
            if p.members:
                for m in p.members:
                    try:
                        cr = m.change_request
                        if hasattr(cr, "status") and str(cr.status) in ("completed", "ChangeRequestStatus.completed"):
                            completed_count += 1
                    except Exception:
                        pass

            output.append(
                {
                    "id": str(p.id),
                    "name": p.name,
                    "goal": p.goal,
                    "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                    "cr_count": cr_count,
                    "completed_cr_count": completed_count,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
            )
        return output
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 2: get_project ──────────────────────────────────────────────────────

@mcp.tool()
async def get_project(token: str, project_id: str) -> dict[str, Any]:
    """
    Get full project detail including all CRs (with status), pending approvals,
    dependency edges, and rollback history. Use before executing or rolling back.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request),
                selectinload(Project.rollbacks),
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        members_out = []
        pending_approvals = []
        for m in project.members:
            cr = m.change_request
            cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
            members_out.append(
                {
                    "pcr_id": str(m.id),
                    "cr_id": str(cr.id),
                    "title": cr.title,
                    "change_type": str(cr.change_type),
                    "status": cr_status,
                    "sequence_order": m.sequence_order,
                    "depends_on": m.depends_on or [],
                    "target_asset_ids": cr.target_asset_ids or [],
                }
            )
            if cr_status in ("draft", "awaiting_approval", "pending_approval"):
                pending_approvals.append(
                    {
                        "cr_id": str(cr.id),
                        "title": cr.title,
                        "awaiting_since": cr.updated_at.isoformat() if cr.updated_at else None,
                    }
                )

        rollbacks_out = [
            {
                "id": str(r.id),
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "trigger": r.trigger.value if hasattr(r.trigger, "value") else str(r.trigger),
                "created_at": r.created_at.isoformat() if hasattr(r, "created_at") and r.created_at else None,
            }
            for r in project.rollbacks
        ]

        return {
            "id": str(project.id),
            "name": project.name,
            "goal": project.goal,
            "description": project.description,
            "status": project.status.value if hasattr(project.status, "value") else str(project.status),
            "template": project.template,
            "last_chat_summary": getattr(project, "last_chat_summary", None),
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "members": members_out,
            "pending_approvals": pending_approvals,
            "rollbacks": rollbacks_out,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 3: get_project_status ───────────────────────────────────────────────

@mcp.tool()
async def get_project_status(token: str, project_id: str) -> dict[str, Any]:
    """
    Get actionable project status: what's pending approval, what's blocked by dependencies,
    and which CRs are ready to execute right now (approved with all deps completed).
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Build a map: pcr_id -> cr_status for dependency resolution
        pcr_status_map: dict[str, str] = {}
        for m in project.members:
            cr = m.change_request
            pcr_status_map[str(m.id)] = (
                cr.status.value if hasattr(cr.status, "value") else str(cr.status)
            )

        pending_approvals = []
        blocking_crs = []
        next_executable_cr_ids = []

        for m in project.members:
            cr = m.change_request
            cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)

            if cr_status in ("draft", "awaiting_approval"):
                pending_approvals.append(
                    {
                        "cr_id": str(cr.id),
                        "title": cr.title,
                        "awaiting_since": (
                            cr.updated_at.isoformat() if cr.updated_at else None
                        ),
                    }
                )

            if cr_status == "approved":
                deps = m.depends_on or []
                unmet = [
                    dep_id
                    for dep_id in deps
                    if pcr_status_map.get(str(dep_id)) != "completed"
                ]
                if unmet:
                    blocking_crs.append(
                        {
                            "cr_id": str(cr.id),
                            "title": cr.title,
                            "blocked_by_pcr_ids": unmet,
                        }
                    )
                else:
                    next_executable_cr_ids.append(str(cr.id))

        return {
            "project_id": str(project.id),
            "status": project.status.value if hasattr(project.status, "value") else str(project.status),
            "pending_approvals": pending_approvals,
            "blocking_crs": blocking_crs,
            "next_executable_cr_ids": next_executable_cr_ids,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 4: get_project_timeline ─────────────────────────────────────────────

@mcp.tool()
async def get_project_timeline(token: str, project_id: str) -> list[dict[str, Any]]:
    """
    Return the execution timeline for a project: all CRs ordered by last-updated time
    with execution outcomes. Use to audit what happened and in what order.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
            .options(
                selectinload(Project.members)
                .selectinload(ProjectChangeRequest.change_request)
                .selectinload(ChangeRequest.requester)
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            return [{"error": "Project not found"}]

        timeline = []
        for m in sorted(
            project.members,
            key=lambda x: x.change_request.updated_at or x.change_request.created_at,
        ):
            cr = m.change_request
            cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
            rolled_back = cr_status == "rolled_back"
            executor_email = None
            try:
                requester = cr.requester
                if requester is not None:
                    executor_email = getattr(requester, "email", None)
            except Exception:
                pass
            timeline.append(
                {
                    "cr_id": str(cr.id),
                    "change_type": str(cr.change_type),
                    "title": cr.title,
                    "executed_at": (
                        cr.updated_at.isoformat() if cr.updated_at else None
                    ),
                    "outcome": cr_status,
                    "rolled_back": rolled_back,
                    "sequence_order": m.sequence_order,
                    "executor": executor_email,
                }
            )
        return timeline
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 5: estimate_project_risk ────────────────────────────────────────────

_DURATION_MAP: dict[str, int] = {
    "patch_os": 10,
    "patch_packages": 10,
    "rotate_credentials": 5,
    "key_rotation": 5,
    "rotate_iam_key": 5,
    "update_firewall": 3,
    "security_group_update": 3,
    "microsegmentation_policy": 3,
    "default": 5,
}


@mcp.tool()
async def estimate_project_risk(token: str, project_id: str) -> dict[str, Any]:
    """
    Estimate execution risk: blast radius (unique assets touched), rollback coverage,
    estimated duration, and highest-risk CR. Use before approving or executing a project.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        all_asset_ids: set[str] = set()
        total_crs = 0
        reversible_crs = 0
        estimated_minutes = 0
        highest_risk_cr = None
        highest_risk_score = -1

        try:
            from app.services.manifest_builder import get_manifest
            manifest = {e["change_type"]: e for e in get_manifest()}
        except Exception:
            manifest = {}

        for m in project.members:
            cr = m.change_request
            total_crs += 1
            asset_ids = cr.target_asset_ids or []
            all_asset_ids.update(str(a) for a in asset_ids)

            ct_str = str(cr.change_type)
            entry = manifest.get(ct_str, {})
            rollback_type = entry.get("rollback_type", "unknown")
            if rollback_type != "permanent":
                reversible_crs += 1

            duration = _DURATION_MAP.get(ct_str, _DURATION_MAP["default"])
            estimated_minutes += duration

            risk_score = len(asset_ids) * 2 + (10 if rollback_type == "permanent" else 0)
            if risk_score > highest_risk_score:
                highest_risk_score = risk_score
                highest_risk_cr = {
                    "cr_id": str(cr.id),
                    "title": cr.title,
                    "change_type": ct_str,
                    "asset_count": len(asset_ids),
                    "rollback_type": rollback_type,
                }

        rollback_coverage_pct = (
            round(reversible_crs / total_crs * 100, 1) if total_crs else 100.0
        )

        return {
            "project_id": project_id,
            "blast_radius_assets": len(all_asset_ids),
            "total_crs": total_crs,
            "rollback_coverage_pct": rollback_coverage_pct,
            "estimated_duration_minutes": estimated_minutes,
            "highest_risk_step": highest_risk_cr,
        }
    finally:
        await db_cm.__aexit__(None, None, None)
