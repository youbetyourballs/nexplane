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
            "change_requests": members_out,
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
            "blast_radius": len(all_asset_ids),
            "blast_radius_assets": len(all_asset_ids),
            "total_crs": total_crs,
            "rollback_coverage_pct": rollback_coverage_pct,
            "estimated_duration_minutes": estimated_minutes,
            "highest_risk_step": highest_risk_cr,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 6: create_project ───────────────────────────────────────────────────

@mcp.tool()
async def create_project(
    token: str,
    name: str,
    goal: str,
    description: str = "",
    template: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a new project in draft status. A project is a named, goal-driven sequence
    of Change Requests. Use chat_with_project to build the plan interactively, then
    add_cr_to_project to assemble the CR sequence.
    """
    from app.models.project import Project, ProjectStatus

    principal, db, db_cm = await _auth(token)
    try:
        from app.models.user import User as _User
        if isinstance(principal, _User):
            actor_id = principal.id
        else:
            actor_id = getattr(principal, "created_by_user_id", None) or principal.id
        project = Project(
            organization_id=principal.organization_id,
            created_by=actor_id,
            name=name,
            goal=goal,
            description=description,
            status=ProjectStatus.draft,
            ai_context=[],
            template=template,
        )
        db.add(project)
        await db.flush()
        await db.commit()
        await db.refresh(project)
        return {
            "id": str(project.id),
            "name": project.name,
            "goal": project.goal,
            "status": project.status.value,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 7: chat_with_project ────────────────────────────────────────────────

@mcp.tool()
async def chat_with_project(
    token: str,
    project_id: str,
    message: str,
) -> dict[str, Any]:
    """
    Send a message to the AI project planner. The AI has full context of the project
    goal and all registered assets. It will ask clarifying questions and eventually
    produce a proposed_crs plan you can materialise with materialize_project_plan.
    Returns {reply, proposed_crs}.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        # Load project
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Load org AI settings
        try:
            from app.models.org_settings import OrganizationSettings
            from app.services.ai_service import AIService, _resolve_provider_config
            from app.services.secrets_service import SecretsService
            from app.config import settings as app_settings
        except ImportError:
            return {"reply": "AI service not configured", "proposed_crs": []}

        settings_result = await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id == user.organization_id
            )
        )
        org_settings = settings_result.scalar_one_or_none()
        if not org_settings or (
            not org_settings.anthropic_api_key_encrypted
            and not org_settings.ai_providers_encrypted
        ):
            return {"reply": "AI service not configured", "proposed_crs": []}

        secrets = SecretsService(app_settings.SECRET_KEY)
        try:
            provider, api_key, model = _resolve_provider_config(org_settings, secrets)
        except ValueError:
            return {"reply": "AI service not configured", "proposed_crs": []}

        # Load assets for context
        assets_result = await db.execute(
            select(Asset)
            .options(selectinload(Asset.connector))
            .where(Asset.organization_id == user.organization_id)
            .limit(50)
        )
        assets = assets_result.scalars().all()
        asset_context = [
            {
                "name": a.name,
                "asset_type": a.asset_type.value if hasattr(a.asset_type, "value") else str(a.asset_type),
                "environment": a.environment.value if hasattr(a.environment, "value") else str(a.environment),
                "criticality": (
                    a.criticality.value
                    if a.criticality and hasattr(a.criticality, "value")
                    else None
                ),
                "connector_type": (
                    a.connector.connector_type.value
                    if a.connector and hasattr(a.connector.connector_type, "value")
                    else None
                ),
                "tags": a.tags or [],
            }
            for a in assets
        ]

        # Build conversation and call AI
        conversation = list(project.ai_context or [])
        conversation.append({"role": "user", "content": message})

        ai_service = AIService(secrets)
        try:
            result_dict = await ai_service.chat(
                provider=provider,
                api_key=api_key,
                model=model,
                conversation=conversation,
                project_goal=project.goal or project.name,
                asset_context=asset_context,
            )
        except Exception as exc:
            logger.exception("AI service error for project %s", project_id)
            return {"error": f"AI service error: {exc}"}

        reply = result_dict.get("reply", "")
        proposed_crs = result_dict.get("proposed_crs")

        # Save conversation + summary
        conversation.append({"role": "assistant", "content": reply})
        project.ai_context = conversation
        if reply:
            project.last_chat_summary = reply[:500]
        await db.commit()

        return {"reply": reply, "proposed_crs": proposed_crs}
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 8: add_cr_to_project ────────────────────────────────────────────────

@mcp.tool()
async def add_cr_to_project(
    token: str,
    project_id: str,
    cr_id: str,
    sequence_order: Optional[int] = None,
    depends_on: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Add an existing Change Request to a project. sequence_order defaults to
    max(existing)+1. depends_on is a list of ProjectChangeRequest IDs that must
    complete before this CR is eligible for execution.
    """
    from sqlalchemy import select
    from app.models.project import Project, ProjectChangeRequest
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        # Load project
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Load CR and verify org ownership
        cr_result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            )
        )
        cr = cr_result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found or belongs to a different organisation"}

        # Determine sequence_order
        if sequence_order is None:
            existing_result = await db.execute(
                select(ProjectChangeRequest).where(
                    ProjectChangeRequest.project_id == project.id
                )
            )
            existing = existing_result.scalars().all()
            max_order = max((m.sequence_order for m in existing), default=0)
            sequence_order = max_order + 1

        pcr = ProjectChangeRequest(
            project_id=project.id,
            change_request_id=cr.id,
            sequence_order=sequence_order,
            depends_on=depends_on or [],
        )
        db.add(pcr)
        await db.flush()
        await db.commit()
        await db.refresh(pcr)

        return {
            "project_id": project_id,
            "cr_id": cr_id,
            "pcr_id": str(pcr.id),
            "sequence_order": pcr.sequence_order,
            "depends_on": pcr.depends_on,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 9: remove_cr_from_project ───────────────────────────────────────────

@mcp.tool()
async def remove_cr_from_project(
    token: str,
    project_id: str,
    cr_id: str,
) -> dict[str, Any]:
    """
    Remove a Change Request from a project. Automatically re-numbers remaining
    members to preserve a contiguous sequence_order starting at 1.
    """
    from sqlalchemy import select
    from app.models.project import Project, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        # Verify project ownership
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Find the PCR to remove
        pcr_result = await db.execute(
            select(ProjectChangeRequest).where(
                ProjectChangeRequest.project_id == project.id,
                ProjectChangeRequest.change_request_id == _uuid.UUID(cr_id),
            )
        )
        pcr = pcr_result.scalar_one_or_none()
        if pcr is None:
            return {"error": "CR is not a member of this project"}

        await db.delete(pcr)
        await db.flush()

        # Re-number remaining members in current sequence_order
        remaining_result = await db.execute(
            select(ProjectChangeRequest)
            .where(ProjectChangeRequest.project_id == project.id)
            .order_by(ProjectChangeRequest.sequence_order)
        )
        remaining = remaining_result.scalars().all()
        updated_sequence = []
        for i, m in enumerate(remaining, start=1):
            m.sequence_order = i
            db.add(m)
            updated_sequence.append({"cr_id": str(m.change_request_id), "sequence_order": i})

        await db.commit()
        return {"removed": True, "updated_sequence": updated_sequence}
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 10: reorder_project_crs ─────────────────────────────────────────────

@mcp.tool()
async def reorder_project_crs(
    token: str,
    project_id: str,
    ordered_cr_ids: list[str],
) -> dict[str, Any]:
    """
    Reorder CRs in a project by providing the desired ordered list of CR IDs.
    sequence_order is set to 1-based index matching the provided list.
    All CR IDs must already be members of the project.
    """
    from sqlalchemy import select
    from app.models.project import Project, ProjectChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Load all PCRs for this project keyed by cr_id
        pcr_result = await db.execute(
            select(ProjectChangeRequest).where(
                ProjectChangeRequest.project_id == project.id
            )
        )
        all_pcrs = pcr_result.scalars().all()
        pcr_by_cr_id = {str(m.change_request_id): m for m in all_pcrs}

        # Validate all provided cr_ids are members
        missing = [cid for cid in ordered_cr_ids if cid not in pcr_by_cr_id]
        if missing:
            return {"error": f"CR IDs not in project: {missing}"}

        updated_sequence = []
        for idx, cr_id in enumerate(ordered_cr_ids, start=1):
            pcr = pcr_by_cr_id[cr_id]
            pcr.sequence_order = idx
            db.add(pcr)
            updated_sequence.append({"cr_id": cr_id, "sequence_order": idx})

        await db.commit()
        return {"updated_sequence": updated_sequence}
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 11: execute_project_phase ───────────────────────────────────────────

@mcp.tool()
async def execute_project_phase(
    token: str,
    project_id: str,
    cr_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Execute one phase of the project. If cr_ids is provided, execute only those
    specific CRs (they must be approved). If omitted, auto-selects all approved
    CRs whose dependencies are all completed. Uses ChangeExecutionService so the
    full workflow lifecycle (preflight, audit, execution) applies.
    Returns {started_executions: [...], skipped: [...]}.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.services.change_execution_service import ChangeExecutionService

    principal, db, db_cm = await _auth(token)
    try:
        from app.models.user import User as _User
        if isinstance(principal, _User):
            actor_id = principal.id
        else:
            actor_id = getattr(principal, "created_by_user_id", None) or principal.id
        proj_result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == principal.organization_id,
            )
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        # Build pcr_id -> cr_status index for dependency resolution
        pcr_status_map: dict[str, str] = {}
        for m in project.members:
            cr = m.change_request
            pcr_status_map[str(m.id)] = (
                cr.status.value if hasattr(cr.status, "value") else str(cr.status)
            )

        if cr_ids is not None:
            # Execute specific CR IDs — find their PCRs
            requested_cr_id_set = set(cr_ids)
            candidates = [
                m for m in project.members
                if str(m.change_request_id) in requested_cr_id_set
            ]
            if not candidates:
                return {"error": "None of the specified cr_ids are members of this project", "started_executions": [], "skipped": []}
        else:
            # Auto-select: approved + all deps completed
            candidates = []
            for m in project.members:
                cr = m.change_request
                cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
                if cr_status != "approved":
                    continue
                deps = m.depends_on or []
                all_deps_done = all(
                    pcr_status_map.get(str(dep_id)) == "completed"
                    for dep_id in deps
                )
                if all_deps_done:
                    candidates.append(m)

        started = []
        skipped = []

        for m in candidates:
            cr = m.change_request
            cr_status = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
            if cr_status != "approved":
                skipped.append(
                    {
                        "cr_id": str(cr.id),
                        "reason": f"CR status is {cr_status!r}, must be 'approved'",
                    }
                )
                continue
            try:
                await ChangeExecutionService.start(
                    cr_id=cr.id,
                    actor_id=actor_id,
                    source="api",
                    db=db,
                )
                started.append({"cr_id": str(cr.id), "status": "executing"})
            except Exception as exc:
                logger.warning(
                    "execute_project_phase: start failed for CR %s: %s", cr.id, exc
                )
                skipped.append({"cr_id": str(cr.id), "reason": str(exc)})

        return {"started_executions": started, "skipped": skipped}
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 12: rollback_project ────────────────────────────────────────────────

@mcp.tool()
async def rollback_project(
    token: str,
    project_id: str,
    notes: Optional[str] = None,
    to_cr_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Initiate a full FILO rollback of a project. Rolls back all completed CRs in
    reverse sequence order. to_cr_id: if provided, rolls back from the most recent CR
    down to and including this CR (FILO order). CRs with sequence_order < to_cr_id's
    order are left untouched. Returns rollback record ID.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.project import Project, ProjectChangeRequest
    from app.services import project_rollback_service as prs

    principal, db, db_cm = await _auth(token)
    try:
        from app.models.user import User as _User
        if isinstance(principal, _User):
            actor_id = principal.id
        else:
            actor_id = getattr(principal, "created_by_user_id", None) or principal.id
        proj_result = await db.execute(
            select(Project)
            .where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == principal.organization_id,
            )
            .options(
                selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        cr_ids_filter = None
        if to_cr_id is not None:
            try:
                to_cr_uuid = _uuid.UUID(to_cr_id)
            except ValueError:
                return {"error": "cr_not_in_project", "cr_id": to_cr_id}

            target_member = next(
                (m for m in project.members if m.change_request_id == to_cr_uuid),
                None,
            )
            if target_member is None:
                return {"error": "cr_not_in_project", "cr_id": to_cr_id}

            from app.models.change_request import ChangeRequestStatus as _CRStatus
            if target_member.change_request.status != _CRStatus.completed:
                return {"error": "cr_not_executed", "cr_id": to_cr_id}

            cr_ids_filter = [
                m.change_request_id
                for m in project.members
                if (
                    m.sequence_order >= target_member.sequence_order
                    and m.change_request.status == _CRStatus.completed
                )
            ]
            if not cr_ids_filter:
                return {"rollback_id": None, "message": "no executed CRs to roll back in range"}

        rollback, warnings = await prs.initiate(
            db=db,
            project=project,
            triggered_by_user_id=actor_id,
            notes=notes,
            cr_ids=cr_ids_filter,
        )

        return {
            "rollback_initiated": True,
            "rollback_id": str(rollback.id),
            "warnings": warnings,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 13: materialize_project_plan ────────────────────────────────────────

@mcp.tool()
async def materialize_project_plan(
    token: str,
    project_id: str,
    proposed_crs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Convert an AI-proposed plan into real CRs attached to the project.
    Each item in proposed_crs must have: change_type, target_assets (list of asset names),
    desired_outcome (dict), seq (int), depends_on (list of seq ints), rationale (str, optional).
    Assets are resolved by name (case-insensitive match on name or hostname).
    Returns {created_crs, errors}.
    """
    from sqlalchemy import select
    from app.models.project import Project, ProjectChangeRequest
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.asset import Asset

    principal, db, db_cm = await _auth(token)
    try:
        from app.models.user import User as _User
        if isinstance(principal, _User):
            actor_id = principal.id
        else:
            actor_id = getattr(principal, "created_by_user_id", None) or principal.id
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == principal.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found", "created_crs": [], "errors": []}

        # Determine the current max sequence_order in the project
        existing_result = await db.execute(
            select(ProjectChangeRequest).where(
                ProjectChangeRequest.project_id == project.id
            )
        )
        existing_members = existing_result.scalars().all()
        existing_max_order = max((m.sequence_order for m in existing_members), default=0)

        # Build seq -> pcr_id map for depends_on resolution (populated as we create)
        seq_to_pcr_id: dict[int, str] = {}

        created_crs = []
        errors = []

        for idx, item in enumerate(proposed_crs):
            try:
                change_type = item.get("change_type")
                target_assets = item.get("target_assets", [])
                desired_outcome = item.get("desired_outcome", {})
                raw_seq = item.get("seq")
                seq = raw_seq if raw_seq is not None else (idx + 1)
                depends_on_seqs = item.get("depends_on", [])
                rationale = item.get("rationale", "")

                if not change_type:
                    errors.append(
                        {"item_index": idx, "error": "missing change_type", "change_type": None}
                    )
                    continue
                if not target_assets:
                    errors.append(
                        {"item_index": idx, "error": "missing target_assets", "change_type": change_type}
                    )
                    continue

                # Resolve first asset by name (ilike on name or hostname)
                asset_name = target_assets[0]
                asset_result = await db.execute(
                    select(Asset).where(
                        Asset.organization_id == principal.organization_id,
                        Asset.name.ilike(asset_name),
                    ).limit(1)
                )
                asset = asset_result.scalar_one_or_none()

                if asset is None:
                    # Try hostname field if exists
                    try:
                        asset_result2 = await db.execute(
                            select(Asset).where(
                                Asset.organization_id == principal.organization_id,
                                Asset.hostname.ilike(asset_name),
                            ).limit(1)
                        )
                        asset = asset_result2.scalar_one_or_none()
                    except Exception:
                        asset = None

                if asset is None:
                    errors.append(
                        {
                            "item_index": idx,
                            "error": f"asset not found: {asset_name!r}",
                            "change_type": change_type,
                        }
                    )
                    continue

                title = f"{change_type} on {asset.name}"
                if rationale:
                    title = f"{title} — {rationale[:80]}"

                # Create the CR
                cr = ChangeRequest(
                    organization_id=principal.organization_id,
                    requester_id=actor_id,
                    change_type=change_type,
                    target_asset_ids=[str(asset.id)],
                    title=title,
                    status=ChangeRequestStatus.draft,
                    desired_outcome=desired_outcome,
                )
                db.add(cr)
                await db.flush()

                # Resolve depends_on seq ints -> pcr_ids
                resolved_depends_on = [
                    seq_to_pcr_id[s] for s in depends_on_seqs if s in seq_to_pcr_id
                ]

                sequence_order = existing_max_order + seq
                pcr = ProjectChangeRequest(
                    project_id=project.id,
                    change_request_id=cr.id,
                    sequence_order=sequence_order,
                    depends_on=resolved_depends_on,
                )
                db.add(pcr)
                await db.flush()

                seq_to_pcr_id[seq] = str(pcr.id)
                created_crs.append(
                    {
                        "cr_id": str(cr.id),
                        "pcr_id": str(pcr.id),
                        "change_type": change_type,
                        "asset_id": str(asset.id),
                        "asset_name": asset.name,
                        "sequence_order": sequence_order,
                    }
                )
            except Exception as exc:
                logger.exception("materialize_project_plan: error on item %d", idx)
                errors.append(
                    {
                        "item_index": idx,
                        "error": str(exc),
                        "change_type": item.get("change_type"),
                    }
                )

        await db.commit()
        return {"created_crs": created_crs, "errors": errors}
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 14: define_success_criteria ─────────────────────────────────────────

@mcp.tool()
async def define_success_criteria(
    token: str,
    project_id: str,
    criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Define success criteria for a project. Each criterion has:
    - type: cr_completed | host_state_check | service_check | port_check | manual
    - description: human-readable label
    - assertion: dict with type-specific fields (cr_id, asset_id, field, value, etc.)
    Returns list of {criteria_id, type, description, assertion}.
    """
    from sqlalchemy import select
    from app.models.project import Project
    from app.models.project_success_criteria import (
        ProjectSuccessCriteria,
        CriteriaType,
    )

    user, db, db_cm = await _auth(token)
    try:
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        valid_types = {ct.value for ct in CriteriaType}
        created = []
        validation_errors = []

        for i, item in enumerate(criteria):
            ctype_str = item.get("type")
            if ctype_str not in valid_types:
                validation_errors.append(
                    {
                        "index": i,
                        "error": f"Invalid type {ctype_str!r}. Valid: {sorted(valid_types)}",
                    }
                )
                continue

            description = item.get("description", "")
            assertion = item.get("assertion", {})

            c = ProjectSuccessCriteria(
                project_id=project.id,
                type=CriteriaType(ctype_str),
                description=description,
                assertion=assertion,
            )
            db.add(c)
            await db.flush()
            created.append(c)

        if validation_errors:
            return {
                "error": "Some criteria had validation errors",
                "validation_errors": validation_errors,
                "criteria_ids": [],
                "count": 0,
            }

        await db.commit()
        for c in created:
            await db.refresh(c)
        return [
            {
                "criteria_id": str(c.id),
                "type": c.type.value if hasattr(c.type, "value") else str(c.type),
                "description": c.description,
                "assertion": c.assertion,
            }
            for c in created
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


# ── TOOL 15: check_success_criteria ──────────────────────────────────────────

@mcp.tool()
async def check_success_criteria(token: str, project_id: str) -> dict[str, Any]:
    """
    Evaluate all success criteria for a project against live platform state.
    Updates last_result and last_checked_at for each criterion in the DB.
    Returns {overall: pass|fail|partial|not_checked, criteria: [...]}.
    Use after execute_project_phase to verify outcomes.
    """
    from sqlalchemy import select
    from app.models.project import Project
    from app.services.success_criteria_service import evaluate_all_criteria

    user, db, db_cm = await _auth(token)
    try:
        proj_result = await db.execute(
            select(Project).where(
                Project.id == _uuid.UUID(project_id),
                Project.organization_id == user.organization_id,
            )
        )
        project = proj_result.scalar_one_or_none()
        if project is None:
            return {"error": "Project not found"}

        raw = await evaluate_all_criteria(
            db=db,
            project_id=project.id,
            org_id=user.organization_id,
        )
        # Normalise to canonical shape expected by callers
        criteria_results = raw.get("criteria_results") or raw.get("criteria") or []
        pass_count = sum(1 for c in criteria_results if c.get("result") == "pass")
        fail_count = sum(1 for c in criteria_results if c.get("result") == "fail")
        pending_count = sum(
            1 for c in criteria_results
            if c.get("result") in ("pending_manual", "not_checked", None)
        )
        return {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pending_count": pending_count,
            "all_pass": fail_count == 0 and pending_count == 0 and pass_count > 0,
            "criteria_results": criteria_results,
            "overall": raw.get("overall"),
        }
    finally:
        await db_cm.__aexit__(None, None, None)
