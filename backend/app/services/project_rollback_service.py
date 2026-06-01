"""Orchestrates project-wide rollback: creates rollback record, runs steps in reverse order,
handles reconstitution for permanent CRs, supports pause/resume and crash recovery."""
import asyncio
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.project import Project, ProjectChangeRequest, ProjectStatus
from app.models.project_rollback import (
    ProjectRollback, ProjectRollbackStep,
    ProjectRollbackStatus, ProjectRollbackTrigger,
    RollbackStepStatus, RollbackKind,
)
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)

RECONSTITUTION_PAIRS: dict[str, list[str]] = {
    "rotate_iam_key":                  ["create_backup", "capture_instance_state"],
    "rotate_ssh_keys":                 ["create_backup"],
    "gcp_rotate_service_account_key":  ["create_backup"],
    "azure_rotate_storage_key":        ["create_backup"],
    "rds_instance_delete":             ["rds_snapshot_create"],
    "ec2_terminate":                   ["snapshot_asset", "capture_instance_state"],
    "iam_user_delete":                 ["capture_instance_state"],
}

_PERMANENT_TYPES_CACHE: set[str] | None = None
_ACTIVE_ROLLBACKS: set[uuid.UUID] = set()


def _get_permanent_types() -> set[str]:
    global _PERMANENT_TYPES_CACHE
    if _PERMANENT_TYPES_CACHE is None:
        from app.services.manifest_builder import get_manifest
        _PERMANENT_TYPES_CACHE = {
            e["change_type"] for e in get_manifest()
            if e.get("rollback_type") == "permanent"
        }
    return _PERMANENT_TYPES_CACHE


def _find_backup_cr(
    cr: ChangeRequest,
    completed_crs: list[ChangeRequest],
) -> ChangeRequest | None:
    """Find a completed pre-capture CR for a permanent CR, searching backwards through the list."""
    candidates = RECONSTITUTION_PAIRS.get(str(cr.change_type), [])
    if not candidates:
        return None
    cr_assets = set(cr.target_asset_ids or [])
    # Find position of the permanent CR; only look at CRs before it
    cr_index = next((i for i, c in enumerate(completed_crs) if c.id == cr.id), len(completed_crs))
    for candidate in reversed(completed_crs[:cr_index]):
        if (
            str(candidate.change_type) in candidates
            and candidate.status == ChangeRequestStatus.completed
            and set(candidate.target_asset_ids or []) & cr_assets
        ):
            return candidate
    return None


def _build_preflight_warnings(
    members: list[ProjectChangeRequest],
    completed_crs: list[ChangeRequest],
    permanent_types: set[str],
) -> list[str]:
    warnings = []
    for m in members:
        cr = m.change_request
        if str(cr.change_type) in permanent_types:
            if not _find_backup_cr(cr, completed_crs):
                warnings.append(
                    f"No backup found for '{cr.change_type}' "
                    f"(CR: {getattr(cr, 'title', None) or str(cr.id)}) — "
                    "will require manual intervention during rollback"
                )
    return warnings


async def initiate(
    db: AsyncSession,
    project: Project,
    triggered_by_user_id: uuid.UUID,
    notes: str | None,
    cr_ids: list[uuid.UUID] | None,
) -> tuple[ProjectRollback, list[str]]:
    """Create a ProjectRollback and steps. Fires background task. Returns (rollback, warnings).

    Requires: project.members and each member.change_request must be eagerly loaded
    before calling (selectinload). Accessing them as lazy loads in async context will raise.
    """
    permanent_types = _get_permanent_types()

    eligible_members = [
        m for m in project.members
        if m.change_request.status == ChangeRequestStatus.completed
    ]
    if cr_ids is not None:
        cr_id_set = set(cr_ids)
        eligible_members = [m for m in eligible_members if m.change_request_id in cr_id_set]

    all_crs = [m.change_request for m in project.members]
    warnings = _build_preflight_warnings(eligible_members, all_crs, permanent_types)

    rollback = ProjectRollback(
        project_id=project.id,
        status=ProjectRollbackStatus.pending,
        trigger=ProjectRollbackTrigger.manual,
        triggered_by_user_id=triggered_by_user_id,
        notes=notes,
    )
    db.add(rollback)
    await db.flush()

    sorted_members = sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)
    for i, member in enumerate(sorted_members):
        cr = member.change_request
        ct = str(cr.change_type)
        if ct in permanent_types:
            backup_cr = _find_backup_cr(cr, all_crs)
            kind = RollbackKind.reconstitution if backup_cr else RollbackKind.permanent_no_backup
            backup_cr_id = backup_cr.id if backup_cr else None
        else:
            kind = RollbackKind.standard
            backup_cr_id = None

        db.add(ProjectRollbackStep(
            project_rollback_id=rollback.id,
            change_request_id=cr.id,
            sequence_order=i + 1,
            status=RollbackStepStatus.pending,
            rollback_kind=kind,
            backup_cr_id=backup_cr_id,
        ))

    project.status = ProjectStatus.rolling_back
    await db.commit()
    await db.refresh(rollback)

    asyncio.ensure_future(_run_rollback(rollback.id))
    return rollback, warnings


async def initiate_auto(
    db: AsyncSession,
    project_id: uuid.UUID,
    cr_id: uuid.UUID,
    trigger: ProjectRollbackTrigger,
) -> ProjectRollback:
    """Create an awaiting_user rollback for auto-triggers. Does NOT start execution."""
    rollback = ProjectRollback(
        project_id=project_id,
        status=ProjectRollbackStatus.awaiting_user,
        trigger=trigger,
        triggered_by_cr_id=cr_id,
    )
    db.add(rollback)
    await db.commit()
    await db.refresh(rollback)
    return rollback


async def pause(db: AsyncSession, rollback: ProjectRollback) -> None:
    rollback.status = ProjectRollbackStatus.paused
    rollback.paused_at = datetime.now(timezone.utc)
    await db.commit()


async def _populate_steps_for_auto_rollback(db: AsyncSession, rollback: ProjectRollback) -> None:
    """Populate steps for an auto-triggered rollback that was created without steps."""
    proj_res = await db.execute(
        select(Project)
        .where(Project.id == rollback.project_id)
        .options(selectinload(Project.members).selectinload(ProjectChangeRequest.change_request))
    )
    project = proj_res.scalar_one_or_none()
    if not project:
        return

    permanent_types = _get_permanent_types()
    eligible_members = [
        m for m in project.members
        if m.change_request.status == ChangeRequestStatus.completed
    ]
    all_crs = [m.change_request for m in project.members]
    sorted_members = sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)

    for i, member in enumerate(sorted_members):
        cr = member.change_request
        ct = str(cr.change_type)
        if ct in permanent_types:
            backup_cr = _find_backup_cr(cr, all_crs)
            kind = RollbackKind.reconstitution if backup_cr else RollbackKind.permanent_no_backup
            backup_cr_id = backup_cr.id if backup_cr else None
        else:
            kind = RollbackKind.standard
            backup_cr_id = None

        db.add(ProjectRollbackStep(
            project_rollback_id=rollback.id,
            change_request_id=cr.id,
            sequence_order=i + 1,
            status=RollbackStepStatus.pending,
            rollback_kind=kind,
            backup_cr_id=backup_cr_id,
        ))

    project.status = ProjectStatus.rolling_back
    await db.flush()


async def resume(rollback_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ProjectRollback)
            .where(ProjectRollback.id == rollback_id)
            .options(selectinload(ProjectRollback.steps))
        )
        rollback = res.scalar_one()
        if not rollback.steps:
            await _populate_steps_for_auto_rollback(db, rollback)
        rollback.status = ProjectRollbackStatus.running
        rollback.paused_at = None
        await db.commit()
    asyncio.ensure_future(_run_rollback(rollback_id))


async def step_decision(
    db: AsyncSession,
    step: ProjectRollbackStep,
    rollback: ProjectRollback,
    action: str,
) -> None:
    if action == "skip":
        step.status = RollbackStepStatus.skipped
        step.result = {"decision": "skipped_by_user"}
        step.completed_at = datetime.now(timezone.utc)
    elif action == "mark_done":
        step.status = RollbackStepStatus.completed
        step.result = {"decision": "marked_done_by_user"}
        step.completed_at = datetime.now(timezone.utc)
    elif action == "retry":
        step.status = RollbackStepStatus.pending
        step.result = None
    await db.commit()
    if action in ("skip", "mark_done", "retry"):
        if rollback.status != ProjectRollbackStatus.running:
            asyncio.ensure_future(_run_rollback(rollback.id))


async def on_cr_failed(project_id: uuid.UUID, cr_id: uuid.UUID) -> None:
    """Called when a CR fails in a project. Creates awaiting_user rollback if none active."""
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(ProjectRollback).where(
                ProjectRollback.project_id == project_id,
                ProjectRollback.status.in_([
                    ProjectRollbackStatus.running,
                    ProjectRollbackStatus.paused,
                    ProjectRollbackStatus.awaiting_user,
                ])
            )
        )
        if existing.scalar_one_or_none():
            return
        await initiate_auto(db, project_id, cr_id, ProjectRollbackTrigger.execution_failure)


async def resume_interrupted() -> None:
    """Called at startup: re-dispatches any rollback stuck in 'running' state."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ProjectRollback).where(ProjectRollback.status == ProjectRollbackStatus.running)
        )
        rollbacks = res.scalars().all()
        for rollback in rollbacks:
            logger.info("Resuming interrupted project rollback %s", rollback.id)
            asyncio.ensure_future(_run_rollback(rollback.id))


async def _run_rollback(rollback_id: uuid.UUID) -> None:
    if rollback_id in _ACTIVE_ROLLBACKS:
        return
    _ACTIVE_ROLLBACKS.add(rollback_id)
    try:
        await _run_rollback_inner(rollback_id)
    finally:
        _ACTIVE_ROLLBACKS.discard(rollback_id)


async def _run_rollback_inner(rollback_id: uuid.UUID) -> None:
    """Background task: drive each step in sequence_order, checking for pause after each."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ProjectRollback)
            .where(ProjectRollback.id == rollback_id)
            .options(selectinload(ProjectRollback.steps))
        )
        rollback = res.scalar_one_or_none()
        if not rollback:
            return

        rollback.status = ProjectRollbackStatus.running
        rollback.started_at = rollback.started_at or datetime.now(timezone.utc)
        await db.commit()

        steps = sorted(rollback.steps, key=lambda s: s.sequence_order)

        for i, step in enumerate(steps):
            await db.refresh(rollback)
            await db.refresh(step)  # re-read step status in case of external changes
            if rollback.status == ProjectRollbackStatus.paused:
                return
            if step.status in (RollbackStepStatus.completed, RollbackStepStatus.skipped):
                continue
            if step.status == RollbackStepStatus.awaiting_user:
                return

            rollback.current_step = i
            step.status = RollbackStepStatus.running
            step.started_at = datetime.now(timezone.utc)
            await db.commit()

            try:
                result_data = await _execute_step(step)
                step.status = RollbackStepStatus.completed
                step.result = result_data
            except Exception as exc:
                logger.error("Rollback step %s failed: %s", step.id, exc)
                step.status = RollbackStepStatus.awaiting_user
                step.result = {"error": str(exc)}

            step.completed_at = datetime.now(timezone.utc)
            await db.commit()

            if step.status == RollbackStepStatus.awaiting_user:
                return

        rollback.status = ProjectRollbackStatus.completed
        rollback.completed_at = datetime.now(timezone.utc)

        proj_res = await db.execute(select(Project).where(Project.id == rollback.project_id))
        project = proj_res.scalar_one_or_none()
        if project and project.status == ProjectStatus.rolling_back:
            project.status = ProjectStatus.in_progress
        await db.commit()


async def _execute_step(step: ProjectRollbackStep) -> dict:
    """Execute one rollback step, optionally merging backup CR result for reconstitution."""
    from app.services.rollback_executor import execute_cr_rollback

    extra: dict | None = None
    if step.rollback_kind == RollbackKind.reconstitution and step.backup_cr_id:
        async with AsyncSessionLocal() as db:
            backup_run_res = await db.execute(
                select(ExecutionRun).where(
                    ExecutionRun.change_request_id == step.backup_cr_id,
                    ExecutionRun.status == ExecutionStatus.completed,
                ).order_by(ExecutionRun.started_at.desc()).limit(1)
            )
            backup_run = backup_run_res.scalar_one_or_none()
            if backup_run and backup_run.result:
                extra = backup_run.result

    return await execute_cr_rollback(step.change_request_id, extra_execution_result=extra)
