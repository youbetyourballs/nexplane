# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel
from app.database import get_db
from app.models.project import Project, ProjectChangeRequest, ProjectStatus
from app.models.project_phase import ProjectPhase
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.user import User, UserRole
from app.models.org_settings import OrganizationSettings
from app.models.project_rollback import ProjectRollback, ProjectRollbackStep, ProjectRollbackStatus
from app.schemas.project_rollback import (
    ProjectRollbackRead, RollbackInitRequest, RollbackInitResponse, StepDecisionRequest,
)
import app.services.project_rollback_service as rollback_svc
from app.models.asset import Asset
from app.routers import current_user
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectRead, ProjectSummary,
    ProjectMemberCreate, ProjectMemberUpdate, ProjectMemberRead, ProjectDetailRead,
)
from app.services.audit_service import record_event
from app.services.ai_service import AIService
from app.services.secrets_service import SecretsService
from app.config import settings as app_settings


def _resolve_asset_ids(proposed_crs: list[dict], name_to_id: dict[str, str]) -> list[dict]:
    """Resolve target_assets names to UUIDs, adding target_asset_ids to each CR dict."""
    for cr in proposed_crs:
        cr["target_asset_ids"] = [
            name_to_id[n] for n in cr.get("target_assets", []) if n in name_to_id
        ]
    return proposed_crs


class AIChatRequest(BaseModel):
    message: str


class AIChatResponse(BaseModel):
    reply: str
    proposed_crs: list[dict] | None = None


router = APIRouter(prefix="/projects", tags=["Projects"])


class ProjectFromTemplateRequest(BaseModel):
    template: str
    name: str
    target_asset_ids: list[str] = []


@router.post("/from-template", status_code=201)
async def create_from_template(
    body: ProjectFromTemplateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.project_template_service import create_project_from_template
    try:
        return await create_project_from_template(
            template_name=body.template,
            project_name=body.name,
            target_asset_ids=body.target_asset_ids,
            user_id=str(user.id),
            organization_id=str(user.organization_id),
            db=db,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/templates")
async def list_templates(user: User = Depends(current_user)):
    from app.services.project_template_service import TEMPLATES
    return [{"name": k, "description": v["description"], "phases": len(v["phases"])} for k, v in TEMPLATES.items()]

_MEMBER_OPTIONS = [
    selectinload(ProjectChangeRequest.change_request).selectinload(
        ChangeRequest.requester
    )
]


def _normalize_uuid(val) -> uuid.UUID:
    return uuid.UUID(val) if isinstance(val, str) else val


def _detect_cycle(
    target_pcr_id: uuid.UUID,
    proposed_depends_on: list[uuid.UUID],
    all_members: list[ProjectChangeRequest],
) -> bool:
    """Returns True if the proposed deps would create a dependency cycle."""
    deps_map: dict[uuid.UUID, list[uuid.UUID]] = {}
    for m in all_members:
        raw = m.depends_on or []
        deps = [_normalize_uuid(d) for d in raw]
        if m.id == target_pcr_id:
            deps_map[m.id] = [_normalize_uuid(d) for d in proposed_depends_on]
        else:
            deps_map[m.id] = deps

    def dfs(node: uuid.UUID, visited: set) -> bool:
        if node == target_pcr_id:
            return True
        if node in visited:
            return False
        visited.add(node)
        return any(dfs(dep, visited) for dep in deps_map.get(node, []))

    return any(dfs(dep, set()) for dep in proposed_depends_on)


def _compute_eligible(pcr: ProjectChangeRequest, all_members: list[ProjectChangeRequest]) -> bool:
    if not pcr.depends_on:
        return True
    pcr_map = {m.id: m for m in all_members}
    for dep_raw in pcr.depends_on:
        dep_id = _normalize_uuid(dep_raw)
        dep = pcr_map.get(dep_id)
        if not dep or dep.change_request.status != ChangeRequestStatus.completed:
            return False
    return True


def _build_member_read(pcr: ProjectChangeRequest, all_members: list[ProjectChangeRequest]) -> dict:
    return {
        "id": pcr.id,
        "project_id": pcr.project_id,
        "change_request_id": pcr.change_request_id,
        "sequence_order": pcr.sequence_order,
        "depends_on": [_normalize_uuid(d) for d in (pcr.depends_on or [])],
        "change_request": pcr.change_request,
        "eligible": _compute_eligible(pcr, all_members),
    }


async def _get_project(db: AsyncSession, project_id: uuid.UUID, org_id: uuid.UUID) -> Project:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.organization_id == org_id)
        .options(selectinload(Project.members).options(*_MEMBER_OPTIONS))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project)
        .where(Project.organization_id == user.organization_id)
        .options(selectinload(Project.members).options(*_MEMBER_OPTIONS))
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    out = []
    for p in projects:
        total = len(p.members)
        completed = sum(
            1 for m in p.members if m.change_request.status == ChangeRequestStatus.completed
        )
        out.append(ProjectSummary(
            **ProjectRead.model_validate(p).model_dump(),
            member_count=total,
            completed_count=completed,
        ))
    return out


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        organization_id=user.organization_id,
        created_by=user.id,
        **body.model_dump(),
    )
    db.add(project)
    await db.flush()
    await record_event(
        db, user.organization_id, "project.created",
        {"project_id": str(project.id), "name": project.name},
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectDetailRead)
async def get_project(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)
    members_read = [_build_member_read(m, project.members) for m in project.members]
    return ProjectDetailRead(
        **ProjectRead.model_validate(project).model_dump(),
        members=members_read,
    )


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.goal is not None:
        project.goal = body.goal
    if body.status is not None:
        project.status = body.status
    project.updated_at = datetime.now(timezone.utc)
    await record_event(
        db, user.organization_id, "project.updated",
        {"project_id": str(project.id), "changes": body.model_dump(exclude_none=True)},
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)
    await record_event(
        db, user.organization_id, "project.deleted",
        {"project_id": str(project_id), "name": project.name},
        actor_id=user.id,
    )
    await db.delete(project)
    await db.commit()


@router.post("/{project_id}/rollback", response_model=RollbackInitResponse, status_code=201)
async def initiate_rollback(
    project_id: uuid.UUID,
    body: RollbackInitRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can initiate rollback")
    from sqlalchemy import select as _select
    proj_res = await db.execute(
        _select(Project)
        .where(Project.id == project_id, Project.organization_id == user.organization_id)
        .options(
            selectinload(Project.members).selectinload(ProjectChangeRequest.change_request)
        )
    )
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == ProjectStatus.rolling_back:
        raise HTTPException(status_code=409, detail="A rollback is already in progress for this project")
    rollback, warnings = await rollback_svc.initiate(
        db, project, user.id, body.notes, body.cr_ids,
    )
    await record_event(
        db, user.organization_id, "project.rollback_initiated",
        {"project_id": str(project_id)},
        actor_id=user.id,
    )
    # Reload rollback with steps eagerly to avoid async greenlet errors during serialization
    from sqlalchemy import select as _select2
    rb_res = await db.execute(
        _select2(ProjectRollback)
        .where(ProjectRollback.id == rollback.id)
        .options(selectinload(ProjectRollback.steps))
    )
    rollback = rb_res.scalar_one()
    return RollbackInitResponse(
        rollback=ProjectRollbackRead.model_validate(rollback),
        warnings=warnings,
    )


@router.get("/{project_id}/rollback", response_model=ProjectRollbackRead)
async def get_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project(db, project_id, user.organization_id)
    from sqlalchemy import select as _select
    res = await db.execute(
        _select(ProjectRollback)
        .where(ProjectRollback.project_id == project_id)
        .options(selectinload(ProjectRollback.steps))
        .order_by(ProjectRollback.created_at.desc())
        .limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No rollback found for this project")
    return rollback


@router.post("/{project_id}/rollback/pause", status_code=204)
async def pause_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can pause rollback")
    await _get_project(db, project_id, user.organization_id)
    from sqlalchemy import select as _select
    res = await db.execute(
        _select(ProjectRollback).where(
            ProjectRollback.project_id == project_id,
            ProjectRollback.status == ProjectRollbackStatus.running,
        ).order_by(ProjectRollback.created_at.desc()).limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No running rollback found")
    await rollback_svc.pause(db, rollback)


@router.post("/{project_id}/rollback/resume", status_code=204)
async def resume_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can resume rollback")
    await _get_project(db, project_id, user.organization_id)
    from sqlalchemy import select as _select
    res = await db.execute(
        _select(ProjectRollback).where(
            ProjectRollback.project_id == project_id,
            ProjectRollback.status.in_([
                ProjectRollbackStatus.paused,
                ProjectRollbackStatus.awaiting_user,
            ]),
        ).order_by(ProjectRollback.created_at.desc()).limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No paused or awaiting rollback found")
    await rollback_svc.resume(rollback.id)


@router.post("/{project_id}/rollback/steps/{step_id}/decision", status_code=204)
async def rollback_step_decision(
    project_id: uuid.UUID,
    step_id: uuid.UUID,
    body: StepDecisionRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can make rollback decisions")
    await _get_project(db, project_id, user.organization_id)
    from sqlalchemy import select as _select
    step_res = await db.execute(_select(ProjectRollbackStep).where(ProjectRollbackStep.id == step_id))
    step = step_res.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Rollback step not found")
    rollback_res = await db.execute(
        _select(ProjectRollback).where(ProjectRollback.id == step.project_rollback_id)
    )
    rollback = rollback_res.scalar_one()
    await rollback_svc.step_decision(db, step, rollback, body.action)


@router.post("/{project_id}/members", response_model=ProjectMemberRead, status_code=201)
async def add_project_member(
    project_id: uuid.UUID,
    body: ProjectMemberCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status != ProjectStatus.draft:
        raise HTTPException(status_code=409, detail="Members can only be added to draft projects")

    # Verify CR belongs to org
    cr = await db.get(ChangeRequest, body.change_request_id)
    if not cr or cr.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Change request not found")

    # Cycle detection — new member has no ID yet, use a fresh UUID as placeholder
    if body.depends_on and _detect_cycle(uuid.uuid4(), body.depends_on, project.members):
        raise HTTPException(status_code=400, detail="Dependency would create a cycle")

    pcr = ProjectChangeRequest(
        project_id=project_id,
        change_request_id=body.change_request_id,
        sequence_order=body.sequence_order if body.sequence_order else len(project.members),
        depends_on=[str(d) for d in body.depends_on],
    )
    db.add(pcr)
    await db.flush()

    # Reload with CR relationship
    result = await db.execute(
        select(ProjectChangeRequest)
        .where(ProjectChangeRequest.id == pcr.id)
        .options(*_MEMBER_OPTIONS)
    )
    pcr_loaded = result.scalar_one()

    await record_event(
        db, user.organization_id, "project.member_added",
        {"project_id": str(project_id), "change_request_id": str(body.change_request_id)},
        actor_id=user.id,
    )
    await db.commit()

    # Reload all members for eligibility computation
    project = await _get_project(db, project_id, user.organization_id)
    return _build_member_read(
        next(m for m in project.members if m.id == pcr_loaded.id),
        project.members,
    )


@router.delete("/{project_id}/members/{pcr_id}", status_code=204)
async def remove_project_member(
    project_id: uuid.UUID,
    pcr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status != ProjectStatus.draft:
        raise HTTPException(status_code=409, detail="Members can only be removed from draft projects")

    pcr = next((m for m in project.members if m.id == pcr_id), None)
    if not pcr:
        raise HTTPException(status_code=404, detail="Project member not found")

    await db.delete(pcr)
    await record_event(
        db, user.organization_id, "project.member_removed",
        {"project_id": str(project_id), "pcr_id": str(pcr_id)},
        actor_id=user.id,
    )
    await db.commit()


@router.patch("/{project_id}/members/{pcr_id}", response_model=ProjectMemberRead)
async def update_project_member(
    project_id: uuid.UUID,
    pcr_id: uuid.UUID,
    body: ProjectMemberUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status not in (ProjectStatus.draft, ProjectStatus.in_progress):
        raise HTTPException(
            status_code=409, detail="Members can only be updated in draft or in-progress projects"
        )

    pcr = next((m for m in project.members if m.id == pcr_id), None)
    if not pcr:
        raise HTTPException(status_code=404, detail="Project member not found")

    if body.depends_on is not None:
        new_deps = [_normalize_uuid(d) for d in body.depends_on]
        if _detect_cycle(pcr_id, new_deps, project.members):
            raise HTTPException(status_code=400, detail="Dependency would create a cycle")
        pcr.depends_on = [str(d) for d in new_deps]

    if body.sequence_order is not None:
        pcr.sequence_order = body.sequence_order

    await db.commit()
    project = await _get_project(db, project_id, user.organization_id)
    return _build_member_read(
        next(m for m in project.members if m.id == pcr_id),
        project.members,
    )


@router.post("/{project_id}/ai/chat", response_model=AIChatResponse)
async def ai_chat(
    project_id: uuid.UUID,
    body: AIChatRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    # Get org API key
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings or (not org_settings.anthropic_api_key_encrypted and not org_settings.ai_providers_encrypted):
        raise HTTPException(
            status_code=402,
            detail="AI not configured — add an AI provider API key in Settings",
        )

    from app.services.ai_service import _resolve_provider_config
    secrets = SecretsService(app_settings.SECRET_KEY)
    provider, api_key, model = _resolve_provider_config(org_settings, secrets)

    # Load asset context
    from sqlalchemy.orm import selectinload as _selectinload
    assets_result = await db.execute(
        select(Asset)
        .options(_selectinload(Asset.connector))
        .where(Asset.organization_id == user.organization_id)
    )
    assets = assets_result.scalars().all()
    asset_context = [
        {
            "name": a.name,
            "asset_type": a.asset_type.value,
            "environment": a.environment.value,
            "criticality": a.criticality.value if a.criticality else None,
            "connector_type": a.connector.connector_type.value if a.connector else None,
            "tags": a.tags or [],
        }
        for a in assets
    ]

    # Append user message to conversation
    conversation = list(project.ai_context or [])
    conversation.append({"role": "user", "content": body.message})

    # Call Claude
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
        import logging
        logging.getLogger(__name__).exception("AI service error for project %s", project_id)
        raise HTTPException(
            status_code=502,
            detail=f"AI service error: {str(exc)}",
        )

    # Append AI reply to conversation and save
    conversation.append({"role": "assistant", "content": result_dict["reply"]})
    project.ai_context = conversation
    await db.commit()

    # Resolve target_assets names to UUIDs using the already-loaded asset list
    if result_dict.get("proposed_crs"):
        name_to_id = {a.name: str(a.id) for a in assets}
        result_dict["proposed_crs"] = _resolve_asset_ids(result_dict["proposed_crs"], name_to_id)

    return AIChatResponse(
        reply=result_dict["reply"],
        proposed_crs=result_dict["proposed_crs"],
    )


@router.get("/{project_id}/ai/prompt-preview", response_model=dict)
async def get_prompt_preview(
    project_id: uuid.UUID,
    draft_message: str | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full assembled prompt string for this project (no AI call)."""
    project = await _get_project(db, project_id, user.organization_id)
    if not (project.goal or project.name):
        raise HTTPException(status_code=400, detail="Project has no goal or name")

    from sqlalchemy.orm import selectinload as _selectinload
    assets_result = await db.execute(
        select(Asset)
        .options(_selectinload(Asset.connector))
        .where(Asset.organization_id == user.organization_id)
    )
    assets = assets_result.scalars().all()
    asset_context = [
        {
            "name": a.name,
            "asset_type": a.asset_type.value,
            "environment": a.environment.value,
            "criticality": a.criticality.value if a.criticality else None,
            "connector_type": a.connector.connector_type.value if a.connector else None,
            "tags": a.tags or [],
        }
        for a in assets
    ]

    secrets = SecretsService(app_settings.SECRET_KEY)
    ai_service = AIService(secrets)

    prompt = ai_service.build_prompt_preview(
        goal=project.goal or project.name,
        asset_context=asset_context,
        conversation=list(project.ai_context or []),
        draft_message=draft_message,
    )
    return {"prompt": prompt}


# ---------------------------------------------------------------------------
# Project phases (staged rollout)
# ---------------------------------------------------------------------------

class ProjectPhaseCreate(BaseModel):
    name: str
    sequence: int = 0
    soak_hours: int = 72


class ProjectPhaseRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    sequence: int
    soak_hours: int
    status: str
    soak_started_at: datetime | None
    soak_completed_at: datetime | None


@router.post("/{project_id}/phases", response_model=ProjectPhaseRead, status_code=201)
async def create_project_phase(
    project_id: uuid.UUID,
    body: ProjectPhaseCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    phase = ProjectPhase(
        project_id=project_id,
        name=body.name,
        sequence=body.sequence,
        soak_hours=body.soak_hours,
    )
    db.add(phase)
    await db.commit()
    return phase


@router.get("/{project_id}/phases", response_model=list[ProjectPhaseRead])
async def list_project_phases(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProjectPhase).where(ProjectPhase.project_id == project_id).order_by(ProjectPhase.sequence)
    )
    return result.scalars().all()


@router.post("/{project_id}/phases/{phase_id}/start-soak", status_code=200)
async def start_phase_soak(
    project_id: uuid.UUID,
    phase_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    phase = await db.get(ProjectPhase, phase_id)
    if not phase:
        raise HTTPException(404, "Phase not found")
    phase.status = "soaking"
    phase.soak_started_at = datetime.now(timezone.utc)
    await db.commit()
    return {"phase_id": str(phase_id), "status": "soaking", "soak_hours": phase.soak_hours}
