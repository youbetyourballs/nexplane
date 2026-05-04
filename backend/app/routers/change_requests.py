import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.approval import Approval, ApprovalDecision
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus, RiskLevel
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.user import User, UserRole
from app.routers import current_user
from app.schemas.approval import ApprovalCreate, ApprovalRead
from app.schemas.change_plan import ChangePlanRead
from app.schemas.change_request import ChangeRequestCreate, ChangeRequestRead, ChangeRequestSummary
from app.schemas.execution_run import ExecutionRunRead
from app.services.audit_service import record_event
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request, check_approval_requirements
from app.workflows import runner as workflow_runner
from app.workflows.execute_change_workflow import execute_change_workflow
from app.compliance.freeze import require_no_active_freeze

router = APIRouter(prefix="/change-requests", tags=["Change Requests"])

_CR_OPTIONS = [
    selectinload(ChangeRequest.requester),
    selectinload(ChangeRequest.change_plan),
    selectinload(ChangeRequest.approvals).selectinload(Approval.approver),
    selectinload(ChangeRequest.execution_runs),
]


async def _get_cr(db: AsyncSession, cr_id: uuid.UUID, org_id: uuid.UUID) -> ChangeRequest:
    result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.id == cr_id, ChangeRequest.organization_id == org_id
        ).options(*_CR_OPTIONS)
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(status_code=404, detail="Change request not found")
    return cr


@router.get("", response_model=list[ChangeRequestSummary])
async def list_change_requests(
    status: str | None = Query(None),
    risk_level: str | None = Query(None),
    change_type: str | None = Query(None),
    asset_id: str | None = Query(None, description="Filter to CRs targeting this asset"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(ChangeRequest).where(
        ChangeRequest.organization_id == user.organization_id
    ).options(selectinload(ChangeRequest.requester)).order_by(ChangeRequest.created_at.desc())

    if status:
        q = q.where(ChangeRequest.status == status)
    if risk_level:
        q = q.where(ChangeRequest.risk_level == risk_level)
    if change_type:
        q = q.where(ChangeRequest.change_type == change_type)

    result = await db.execute(q)
    crs = result.scalars().all()

    # asset_id filter applied in Python (target_asset_ids is a JSON array of strings)
    if asset_id:
        crs = [cr for cr in crs if asset_id in (cr.target_asset_ids or [])]

    return crs


@router.post("", response_model=ChangeRequestRead, status_code=201)
async def create_change_request(
    body: ChangeRequestCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=body.title,
        description=body.description,
        change_type=body.change_type,
        target_asset_ids=[str(aid) for aid in body.target_asset_ids],
        desired_outcome=body.desired_outcome,
        status=ChangeRequestStatus.draft,
    )
    db.add(cr)
    await db.flush()
    await record_event(db, user.organization_id, "change_request.created",
                       {"change_request_id": str(cr.id), "title": cr.title, "change_type": cr.change_type.value},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    result = await db.execute(
        select(ChangeRequest).where(ChangeRequest.id == cr.id).options(*_CR_OPTIONS)
    )
    return result.scalar_one()


@router.get("/{cr_id}", response_model=ChangeRequestRead)
async def get_change_request(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_cr(db, cr_id, user.organization_id)


@router.post("/{cr_id}/plan", response_model=ChangePlanRead)
async def generate_change_plan(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status not in (ChangeRequestStatus.draft, ChangeRequestStatus.planned):
        raise HTTPException(status_code=400, detail=f"Cannot plan a change request in status '{cr.status.value}'")

    # Clear stale approvals so a re-planned CR can be approved fresh
    await db.execute(
        select(Approval).where(Approval.change_request_id == cr.id)
    )
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(Approval).where(Approval.change_request_id == cr.id))

    asset_ids = [uuid.UUID(aid) for aid in (cr.target_asset_ids or [])]
    assets_result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
    )
    assets = list(assets_result.scalars().all())

    safety_result = score_change_request(cr, assets)

    if safety_result.is_blocked:
        raise HTTPException(
            status_code=422,
            detail={"message": "Safety review blocked plan generation", "blocking_issues": safety_result.blocking_issues},
        )

    plan_data = generate_plan(cr, assets, safety_result)

    if cr.change_plan:
        plan = cr.change_plan
        plan.generated_steps = plan_data.generated_steps
        plan.preflight_checks = plan_data.preflight_checks
        plan.blast_radius = plan_data.blast_radius
        plan.rollback_plan = plan_data.rollback_plan
        plan.verification_plan = plan_data.verification_plan
    else:
        plan = ChangePlan(
            change_request_id=cr.id,
            generated_steps=plan_data.generated_steps,
            preflight_checks=plan_data.preflight_checks,
            blast_radius=plan_data.blast_radius,
            rollback_plan=plan_data.rollback_plan,
            verification_plan=plan_data.verification_plan,
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan)

    cr.risk_level = safety_result.risk_level
    cr.status = ChangeRequestStatus.planned
    cr.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await record_event(db, user.organization_id, "change_plan.generated",
                       {"change_request_id": str(cr.id), "risk_level": safety_result.risk_level.value,
                        "risk_score": safety_result.risk_score, "risk_factors": [f.name for f in safety_result.risk_factors],
                        "warnings": safety_result.warnings},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.post("/{cr_id}/submit-for-approval", response_model=ChangeRequestRead)
async def submit_for_approval(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status != ChangeRequestStatus.planned:
        raise HTTPException(status_code=400, detail="Change request must be in 'planned' status to submit for approval")

    if not cr.change_plan:
        raise HTTPException(status_code=400, detail="No change plan exists. Generate a plan first.")

    cr.status = ChangeRequestStatus.awaiting_approval
    cr.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await record_event(db, user.organization_id, "change_request.submitted_for_approval",
                       {"change_request_id": str(cr.id), "risk_level": cr.risk_level.value},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    result = await db.execute(
        select(ChangeRequest).where(ChangeRequest.id == cr.id).options(*_CR_OPTIONS)
    )
    return result.scalar_one()


@router.post("/{cr_id}/approve", response_model=ApprovalRead, dependencies=[Depends(require_no_active_freeze)])
async def approve_change_request(
    cr_id: uuid.UUID,
    body: ApprovalCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status != ChangeRequestStatus.awaiting_approval:
        raise HTTPException(status_code=400, detail="Change request is not awaiting approval")

    if user.role not in (UserRole.approver, UserRole.admin):
        raise HTTPException(status_code=403, detail="Only approvers and admins can approve change requests")

    existing = await db.execute(
        select(Approval).where(Approval.change_request_id == cr.id, Approval.approver_id == user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You have already submitted an approval decision")

    approval = Approval(
        change_request_id=cr.id,
        approver_id=user.id,
        decision=body.decision,
        comment=body.comment,
    )
    db.add(approval)
    await db.flush()

    all_approvals_result = await db.execute(
        select(Approval).where(Approval.change_request_id == cr.id).options(selectinload(Approval.approver))
    )
    all_approvals = list(all_approvals_result.scalars().all())

    req_check = check_approval_requirements(cr.risk_level, all_approvals)

    if body.decision == ApprovalDecision.approved and req_check["satisfied"]:
        cr.status = ChangeRequestStatus.approved
        cr.updated_at = datetime.now(timezone.utc)

        # Check if a maintenance window is required and currently open
        from app.models.maintenance_window import MaintenanceWindow
        from app.routers.maintenance_windows import is_window_open_for_org

        windows_result = await db.execute(
            select(MaintenanceWindow).where(
                MaintenanceWindow.organization_id == user.organization_id,
                MaintenanceWindow.enabled == True,
            )
        )
        windows = windows_result.scalars().all()

        if windows:
            # Only gate if there are maintenance windows configured
            asset_ids = [uuid.UUID(aid) for aid in (cr.target_asset_ids or [])]
            assets_result = await db.execute(
                select(Asset).where(Asset.id.in_(asset_ids))
            )
            assets = list(assets_result.scalars().all())
            asset_tags: set[str] = set()
            for asset in assets:
                if asset.tags:
                    asset_tags.update(asset.tags if isinstance(asset.tags, list) else [])
            now = datetime.now(timezone.utc)
            if not is_window_open_for_org(windows, asset_tags, now):
                cr.status = ChangeRequestStatus.queued_for_maintenance

    await record_event(db, user.organization_id, f"change_request.{body.decision.value}",
                       {"change_request_id": str(cr.id), "approver": user.email,
                        "comment": body.comment, "approval_satisfied": req_check["satisfied"]},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    result = await db.execute(
        select(Approval).where(Approval.id == approval.id).options(selectinload(Approval.approver))
    )
    return result.scalar_one()


@router.post("/{cr_id}/reject", response_model=ApprovalRead)
async def reject_change_request(
    cr_id: uuid.UUID,
    body: ApprovalCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status != ChangeRequestStatus.awaiting_approval:
        raise HTTPException(status_code=400, detail="Change request is not awaiting approval")

    if user.role not in (UserRole.approver, UserRole.admin):
        raise HTTPException(status_code=403, detail="Only approvers and admins can reject change requests")

    approval = Approval(
        change_request_id=cr.id,
        approver_id=user.id,
        decision=ApprovalDecision.rejected,
        comment=body.comment,
    )
    db.add(approval)
    cr.status = ChangeRequestStatus.rejected
    cr.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await record_event(db, user.organization_id, "change_request.rejected",
                       {"change_request_id": str(cr.id), "approver": user.email, "comment": body.comment},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    result = await db.execute(
        select(Approval).where(Approval.id == approval.id).options(selectinload(Approval.approver))
    )
    return result.scalar_one()


CANCELABLE_STATUSES = {
    ChangeRequestStatus.draft,
    ChangeRequestStatus.planned,
    ChangeRequestStatus.awaiting_approval,
    ChangeRequestStatus.approved,
    ChangeRequestStatus.queued_for_maintenance,
}


@router.post("/{cr_id}/cancel", response_model=ChangeRequestRead)
async def cancel_change_request(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status not in CANCELABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a change request in status '{cr.status.value}'"
        )

    # Requesters can cancel their own; approvers and admins can cancel any
    is_owner = cr.requester_id == user.id
    is_privileged = user.role in (UserRole.approver, UserRole.admin)
    if not (is_owner or is_privileged):
        raise HTTPException(status_code=403, detail="Only the requester, approvers, or admins can cancel a change request")

    cr.status = ChangeRequestStatus.rejected
    cr.updated_at = datetime.now(timezone.utc)

    await record_event(db, user.organization_id, "change_request.cancelled",
                       {"change_request_id": str(cr.id), "cancelled_by": user.email},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()
    await db.refresh(cr)
    return cr


@router.post("/{cr_id}/execute", response_model=ExecutionRunRead, dependencies=[Depends(require_no_active_freeze)])
async def execute_change_request(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status != ChangeRequestStatus.approved:
        raise HTTPException(status_code=400, detail="Change request must be approved before execution")

    if cr.risk_level == RiskLevel.critical:
        all_approvals_result = await db.execute(
            select(Approval).where(Approval.change_request_id == cr.id).options(selectinload(Approval.approver))
        )
        all_approvals = list(all_approvals_result.scalars().all())
        req_check = check_approval_requirements(cr.risk_level, all_approvals)
        if req_check.get("no_auto_execute"):
            raise HTTPException(
                status_code=400,
                detail="Critical risk changes require manual execution authorization. Contact your security administrator."
            )

    attempt = len(cr.execution_runs) + 1
    workflow_id = f"wf-cr-{cr.id}-{attempt}"
    run = ExecutionRun(
        change_request_id=cr.id,
        workflow_id=workflow_id,
        status=ExecutionStatus.pending,
    )
    db.add(run)
    await db.flush()
    run_id = run.id

    await record_event(db, user.organization_id, "execution.initiated",
                       {"change_request_id": str(cr.id), "workflow_id": workflow_id, "run_id": str(run_id)},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    wf_input = workflow_runner.WorkflowInput(
        change_request_id=str(cr.id),
        organization_id=str(cr.organization_id),
        initiator_id=str(user.id),
    )
    await workflow_runner.start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)

    result = await db.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
    return result.scalar_one()


@router.post("/{cr_id}/rollback", response_model=ExecutionRunRead)
async def manual_rollback(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status not in (ChangeRequestStatus.completed, ChangeRequestStatus.failed):
        raise HTTPException(status_code=400, detail="Can only manually roll back completed or failed change requests")

    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can initiate manual rollback")

    # Use the completed run — it has the execution result with resolved values like instance_id.
    # Fall back to the most recent run if no completed run exists.
    completed_run_result = await db.execute(
        select(ExecutionRun).where(
            ExecutionRun.change_request_id == cr.id,
            ExecutionRun.status == ExecutionStatus.completed,
        ).order_by(ExecutionRun.started_at.desc()).limit(1)
    )
    latest_run = completed_run_result.scalar_one_or_none()
    if not latest_run:
        fallback = await db.execute(
            select(ExecutionRun).where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        latest_run = fallback.scalar_one_or_none()

    if not latest_run:
        raise HTTPException(status_code=400, detail="No execution run found to roll back")

    rollback_workflow_id = f"wf-rollback-{cr.id}-{uuid.uuid4()}"
    rollback_run = ExecutionRun(
        change_request_id=cr.id,
        workflow_id=rollback_workflow_id,
        status=ExecutionStatus.rolling_back,
    )
    db.add(rollback_run)
    cr.status = ChangeRequestStatus.rolling_back if hasattr(ChangeRequestStatus, 'rolling_back') else ChangeRequestStatus.executing
    cr.updated_at = datetime.now(timezone.utc)

    await db.flush()
    rollback_run_id = rollback_run.id
    await record_event(db, user.organization_id, "rollback.manual_initiated",
                       {"change_request_id": str(cr.id), "initiator": user.email},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    # Run the rollback asynchronously so the endpoint returns immediately
    import asyncio
    from app.workflows.activities import activity_execute_rollback

    plan = cr.change_plan
    execution_result = latest_run.result or {}

    async def _do_rollback():
        from app.database import AsyncSessionLocal
        try:
            rollback_result = await activity_execute_rollback(
                str(cr.id), plan.generated_steps if plan else [], execution_result
            )
            async with AsyncSessionLocal() as s:
                run = await s.get(ExecutionRun, rollback_run_id)
                cr2 = await s.get(ChangeRequest, cr.id)
                if run:
                    run.status = ExecutionStatus.rolled_back
                    run.result = rollback_result
                if cr2:
                    cr2.status = ChangeRequestStatus.rolled_back
                    cr2.updated_at = datetime.now(timezone.utc)
                await s.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Manual rollback failed: %s", exc)
            async with AsyncSessionLocal() as s:
                run = await s.get(ExecutionRun, rollback_run_id)
                cr2 = await s.get(ChangeRequest, cr.id)
                if run:
                    run.status = ExecutionStatus.failed
                    run.result = {"error": str(exc)}
                if cr2:
                    cr2.status = ChangeRequestStatus.failed
                await s.commit()

    asyncio.ensure_future(_do_rollback())

    result = await db.execute(select(ExecutionRun).where(ExecutionRun.id == rollback_run_id))
    return result.scalar_one()


@router.get("/{cr_id}/progress")
async def get_change_request_progress(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns step_metadata for fleet change types — used for live batch progress polling."""
    cr = await _get_cr(db, cr_id, user.organization_id)
    return {"step_metadata": cr.step_metadata or {}, "status": cr.status}
