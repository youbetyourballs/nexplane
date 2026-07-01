# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from croniter import croniter

logger = logging.getLogger(__name__)

# Set by the scheduler on startup via set_scheduler()
_scheduler = None


def set_scheduler(scheduler_instance) -> None:
    global _scheduler
    _scheduler = scheduler_instance


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _policy_allows(
    policy,
    change_type: str,
    risk_level: str,
) -> tuple[bool, str | None]:
    """Return (allowed, reason_or_None). reason is None when allowed."""
    if policy is None:
        return False, "no policy attached to recurring job; manual approval required"
    if not policy.enabled:
        return False, "recurring job policy is disabled"
    now = datetime.now(tz=timezone.utc)
    if policy.expires_at and policy.expires_at <= now:
        return False, f"policy expired at {policy.expires_at.isoformat()}"
    if not policy.allowed_change_types or change_type not in policy.allowed_change_types:
        return False, f"change_type '{change_type}' not in policy allowed_change_types"
    if _RISK_ORDER.get(risk_level, 999) > _RISK_ORDER.get(policy.max_risk_level, 0):
        return False, f"risk_level '{risk_level}' exceeds policy max_risk_level '{policy.max_risk_level}'"
    return True, None


def compute_next_run(cron_expression: str) -> datetime:
    it = croniter(cron_expression, datetime.now(tz=timezone.utc))
    return it.get_next(datetime)


def _apscheduler_id(job_id: str) -> str:
    return f"recurring_job_{job_id}"


def register_job(job) -> None:
    if _scheduler is None:
        return
    from apscheduler.triggers.cron import CronTrigger
    _scheduler.add_job(
        _fire_recurring_job,
        CronTrigger.from_crontab(job.cron_expression, timezone="UTC"),
        id=_apscheduler_id(str(job.id)),
        args=[str(job.id)],
        replace_existing=True,
    )
    logger.info(
        f"Registered recurring job {job.id} ({job.name}) with cron '{job.cron_expression}'"
    )


def deregister_job(job_id: str) -> None:
    if _scheduler is None:
        return
    apscheduler_id = _apscheduler_id(job_id)
    if _scheduler.get_job(apscheduler_id):
        _scheduler.remove_job(apscheduler_id)
        logger.info(f"Deregistered recurring job {job_id}")


async def fire_job_now(job) -> None:
    """Immediately fire a recurring job outside its schedule (e.g. manual trigger)."""
    await _fire_recurring_job(str(job.id))


async def _fire_recurring_job(job_id: str) -> None:
    """Core handler: create a CR, auto-approve it, and trigger execution."""
    from app.database import AsyncSessionLocal
    from app.models.recurring_job import RecurringJob
    from app.models.change_request import (
        ChangeRequest,
        ChangeRequestStatus,
        ChangeType,
        RiskLevel,
    )
    from app.models.approval import Approval, ApprovalDecision
    from app.services.change_plan_service import plan_cr

    _allowed = False  # default before policy check; set inside session
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringJob).where(RecurringJob.id == uuid.UUID(job_id))
        )
        job = result.scalar_one_or_none()
        if not job or not job.enabled:
            logger.info(f"Recurring job {job_id} skipped (not found or disabled)")
            return

        # Map action_id to a ChangeType; fall back to ssm_command for unknown values
        try:
            change_type = ChangeType(job.action_id)
        except ValueError:
            logger.warning(
                f"Recurring job {job_id}: action_id '{job.action_id}' is not a valid ChangeType, "
                f"defaulting to ssm_command"
            )
            change_type = ChangeType.ssm_command

        cr = ChangeRequest(
            organization_id=job.organization_id,
            requester_id=job.created_by,
            title=job.name,
            description=f"Auto-created by recurring job: {job.target_description}",
            change_type=change_type,
            risk_level=RiskLevel.low,
            desired_outcome=job.parameters,
            target_asset_ids=[],
            status=ChangeRequestStatus.draft,
            source="recurring_job",
        )
        db.add(cr)
        await db.flush()

        # Attempt plan generation; if it fails, advance status manually so approval can proceed
        try:
            await plan_cr(db, cr)
        except Exception as e:
            logger.warning(
                f"Recurring job {job_id}: plan generation failed: {e}. Setting status to planned."
            )
            cr.status = ChangeRequestStatus.planned

        # Policy-gated auto-approval
        _policy = None
        if job.policy_id:
            from app.models.recurring_job_policy import RecurringJobPolicy
            _policy = await db.get(RecurringJobPolicy, job.policy_id)

        _risk_level = cr.risk_level.value if hasattr(cr.risk_level, "value") else str(cr.risk_level)
        _allowed, _reason = _policy_allows(_policy, str(change_type.value), _risk_level)
        if _allowed:
            approval = Approval(
                change_request_id=cr.id,
                approver_id=_policy.approved_by if _policy is not None else job.created_by,
                decision=ApprovalDecision.approved,
                comment=f"Auto-approved by recurring job policy {job.policy_id}",
            )
            db.add(approval)
            cr.status = ChangeRequestStatus.approved
        else:
            logger.warning(
                "Recurring job %s: auto-approval denied by policy: %s. CR %s awaits manual approval.",
                job_id, _reason, cr.id,
            )
            cr.status = ChangeRequestStatus.awaiting_approval
        await db.flush()

        # Record run metadata on the job
        job.last_run_at = datetime.now(tz=timezone.utc)
        job.last_cr_id = cr.id
        job.next_run_at = compute_next_run(job.cron_expression)

        await db.commit()

    # Trigger execution outside the session so the CR row is visible to the workflow
    if _allowed:
        try:
            from app.services.change_execution_service import ChangeExecutionService
            from app.database import AsyncSessionLocal as _ASL
            async with _ASL() as exec_db:
                await ChangeExecutionService.start(cr.id, job.created_by, "recurring_job", exec_db)
            logger.info(
                f"Recurring job {job_id} fired successfully, CR {cr.id} executing"
            )
        except Exception as e:
            logger.error(
                f"Recurring job {job_id}: failed to trigger workflow for CR {cr.id}: {e}"
            )
    else:
        logger.info(
            "Recurring job %s: CR %s in awaiting_approval, skipping execution (manual approval required)",
            job_id, cr.id,
        )
