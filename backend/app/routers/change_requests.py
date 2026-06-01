import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.approval import Approval, ApprovalDecision
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus, RiskLevel
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.user import User, UserRole
from app.routers import current_user, require_roles
from app.schemas.approval import ApprovalCreate, ApprovalRead
from app.schemas.change_plan import ChangePlanRead
from app.schemas.change_request import (
    ChangeRequestCreate, ChangeRequestRead, ChangeRequestSummary,
    BatchCreateRequest, BatchCreateResponse, BulkApproveRequest,
)
from app.schemas.execution_run import ExecutionRunRead
from app.services.audit_service import record_event
from app.services.change_plan_service import plan_cr as _plan_cr, PlanBlockedError
from app.services.safety_engine import check_approval_requirements
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
        finding_ids=body.finding_ids or [],
        status=ChangeRequestStatus.draft,
    )
    db.add(cr)
    await db.flush()
    await record_event(db, user.organization_id, "change_request.created",
                       {"change_request_id": str(cr.id), "title": cr.title, "change_type": cr.change_type.value},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()

    if getattr(body, 'access_expiry_hours', None) and body.access_expiry_hours > 0:
        try:
            import logging as _logging
            _logger = _logging.getLogger(__name__)
            from app.services.scheduled_cr_service import schedule_reversal_cr
            rollback_cr_id = await schedule_reversal_cr(
                original_parameters=body.desired_outcome or {},
                original_asset_ids=[str(a) for a in (body.target_asset_ids or [])],
                execute_after_hours=body.access_expiry_hours,
                change_type=body.change_type.value + "_rollback",
                organization_id=str(cr.organization_id),
            )
            cr.scheduled_rollback_cr_id = uuid.UUID(rollback_cr_id)
            await db.commit()
        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"Failed to schedule rollback CR: {_e}")

    result = await db.execute(
        select(ChangeRequest).where(ChangeRequest.id == cr.id).options(*_CR_OPTIONS)
    )
    return result.scalar_one()


@router.post("/batch", response_model=BatchCreateResponse, status_code=201)
async def batch_create_change_requests(
    body: BatchCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    batch_id = uuid.uuid4()
    cr_ids = []
    for item in body.items:
        cr = ChangeRequest(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            requester_id=user.id,
            title=item.title,
            change_type=item.change_type,
            target_asset_ids=item.target_asset_ids,
            desired_outcome=item.desired_outcome,
            finding_ids=item.finding_ids or [],
            snapshot_before=item.snapshot_before,
            verification_checks=item.verification_checks,
            batch_id=batch_id,
            status=ChangeRequestStatus.draft,
        )
        db.add(cr)
        cr_ids.append(cr.id)
    await db.commit()
    return BatchCreateResponse(batch_id=batch_id, cr_ids=cr_ids)


@router.post("/bulk-approve", status_code=200)
async def bulk_approve(
    body: BulkApproveRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    _role = getattr(user, "role", None)
    _role_val = _role.value if hasattr(_role, "value") else str(_role)
    if _role_val not in ("approver", "admin"):
        raise HTTPException(status_code=403, detail="Approver role required")
    approved_ids = []
    for cr_id in body.cr_ids:
        cr = await db.get(ChangeRequest, cr_id)
        if cr and cr.organization_id == user.organization_id and cr.status == ChangeRequestStatus.awaiting_approval:
            risk = getattr(cr, "risk_level", None)
            if risk == RiskLevel.critical and body.decision == "approved":
                continue  # Cannot bulk-approve critical risk
            if body.decision == "approved":
                cr.status = ChangeRequestStatus.approved
            else:
                cr.status = ChangeRequestStatus.rejected
            approved_ids.append(str(cr_id))
    await db.commit()
    return {"approved_count": len(approved_ids), "cr_ids": approved_ids}


@router.post("/cleanup-stuck", dependencies=[Depends(require_roles(UserRole.admin))])
async def cleanup_stuck_change_requests(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all executing/verifying CRs as failed.

    CRs stuck in these states were orphaned when the backend restarted
    mid-execution. Call this to clear phantom activity from the dashboard.
    """
    result = await db.execute(
        update(ChangeRequest)
        .where(
            ChangeRequest.status.in_([
                ChangeRequestStatus.executing,
                ChangeRequestStatus.verifying,
            ]),
            ChangeRequest.organization_id == current_user.organization_id,
        )
        .values(status=ChangeRequestStatus.failed, updated_at=datetime.now(timezone.utc))
        .returning(ChangeRequest.id, ChangeRequest.title)
    )
    cleaned = [{"id": str(r[0]), "title": r[1]} for r in result.fetchall()]
    await db.commit()
    return {"cleaned": len(cleaned), "change_requests": cleaned}


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
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(Approval).where(Approval.change_request_id == cr.id))

    try:
        result = await _plan_cr(db, cr)
    except PlanBlockedError as e:
        raise HTTPException(
            status_code=422,
            detail={"message": "Safety review blocked plan generation", "blocking_issues": e.blocking_issues},
        )

    plan = result.plan
    await record_event(db, user.organization_id, "change_plan.generated",
                       {"change_request_id": str(cr.id),
                        "risk_level": result.risk_level,
                        "risk_score": result.risk_score,
                        "risk_factors": result.risk_factors,
                        "warnings": result.warnings},
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

    # Notify all approvers and admins in the organization
    from app.services.notification_service import NotificationService, NotificationEvent
    _approvers = (await db.execute(
        select(User).where(
            User.organization_id == cr.organization_id,
            User.role.in_([UserRole.approver, UserRole.admin]),
        )
    )).scalars().all()
    if _approvers:
        _notif_svc = NotificationService(db)
        await _notif_svc.emit(NotificationEvent(
            event_type="cr.awaiting_approval",
            organization_id=str(cr.organization_id),
            actor_id=str(user.id),
            resource_id=str(cr.id),
            resource_type="change_request",
            message=f"Change request '{cr.title}' is awaiting your approval",
            recipients=[str(u.id) for u in _approvers],
        ))

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

    # Check hard-enforcement maintenance windows for non-emergency CRs
    _priority = getattr(cr, "priority", None)
    if _priority != "emergency":
        from app.services.maintenance_window_service import is_in_maintenance_window
        for _asset_id in (cr.target_asset_ids or []):
            _asset = await db.get(Asset, uuid.UUID(str(_asset_id)))
            _tags = (_asset.tags or []) if _asset else []
            _blocking = await is_in_maintenance_window(db, _tags, enforcement="hard")
            if _blocking:
                raise HTTPException(
                    status_code=423,
                    detail=f"maintenance_window: execution blocked by hard-enforcement window '{_blocking.name}'"
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
    import logging
    from app.services.rollback_executor import execute_cr_rollback

    _logger = logging.getLogger(__name__)

    async def _do_rollback():
        from app.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as s:
                result_data = await execute_cr_rollback(cr.id, s)
                run2 = await s.get(ExecutionRun, rollback_run_id)
                if run2:
                    run2.status = ExecutionStatus.rolled_back
                    run2.result = result_data
                await s.commit()
        except Exception as exc:
            _logger.error("Manual rollback failed: %s", exc)
            async with AsyncSessionLocal() as s:
                run2 = await s.get(ExecutionRun, rollback_run_id)
                cr2 = await s.get(ChangeRequest, cr.id)
                if run2:
                    run2.status = ExecutionStatus.failed
                    run2.result = {"error": str(exc)}
                if cr2:
                    cr2.status = ChangeRequestStatus.failed
                await s.commit()

    asyncio.ensure_future(_do_rollback())

    result = await db.execute(select(ExecutionRun).where(ExecutionRun.id == rollback_run_id))
    return result.scalar_one()


@router.post("/{cr_id}/confirm-stateful", status_code=200)
async def confirm_stateful(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve the stateful classification gate in an agent_containerize_auto CR.

    Sets stateful_approved_at so the executor polling loop can proceed to build.
    """
    from datetime import datetime, timezone as _tz
    from app.models.change_request import ChangeRequestStatus

    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.change_type.value != "agent_containerize_auto":
        raise HTTPException(
            status_code=400,
            detail="confirm-stateful is only valid for agent_containerize_auto change requests"
        )
    _terminal = {ChangeRequestStatus.completed, ChangeRequestStatus.failed, ChangeRequestStatus.rolled_back}
    if cr.status in _terminal:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm stateful gate on a terminal CR (status: {cr.status.value})"
        )
    if cr.stateful_approved_at is not None:
        return {
            "message": "already confirmed",
            "stateful_approved_at": cr.stateful_approved_at.isoformat(),
        }

    cr.stateful_approved_at = datetime.now(_tz.utc)
    await record_event(
        db, user.organization_id, "containerize_auto.stateful_confirmed",
        {"change_request_id": str(cr.id)},
        actor_id=user.id,
        change_request_id=cr.id,
    )
    await db.commit()
    return {
        "message": "stateful classification confirmed",
        "stateful_approved_at": cr.stateful_approved_at.isoformat(),
    }


@router.get("/{cr_id}/progress")
async def get_change_request_progress(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns step_metadata for fleet change types — used for live batch progress polling."""
    cr = await _get_cr(db, cr_id, user.organization_id)
    return {"step_metadata": cr.step_metadata or {}, "status": cr.status}
