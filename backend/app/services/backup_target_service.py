# backend/app/services/backup_target_service.py
from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backup_target import BackupTarget, BackupTargetStatus

logger = logging.getLogger(__name__)


def compute_status(bt: BackupTarget) -> BackupTargetStatus:
    if bt.recurring_job_id is None:
        return BackupTargetStatus.unprotected
    if bt.last_successful_at is None:
        return BackupTargetStatus.overdue
    window = timedelta(hours=bt.expected_cadence_hours * 1.1)
    if datetime.now(tz=timezone.utc) - bt.last_successful_at <= window:
        return BackupTargetStatus.healthy
    return BackupTargetStatus.overdue


async def auto_create_for_job(db: AsyncSession, job) -> BackupTarget:
    bt = BackupTarget(
        organization_id=job.organization_id,
        recurring_job_id=job.id,
        target_description=job.target_description,
        expected_cadence_hours=_cadence_from_cron(job.cron_expression),
        status=BackupTargetStatus.overdue,
    )
    db.add(bt)
    await db.flush()
    logger.info(f"Auto-created BackupTarget {bt.id} for RecurringJob {job.id}")
    return bt


def _cadence_from_cron(cron_expression: str) -> int:
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return 24
    _, hour, day_of_month, month, day_of_week = parts
    if hour == "*":
        return 1
    if day_of_week != "*":
        return 168
    if day_of_month != "*":
        return 24 * 30
    return 24


async def on_backup_cr_completed(
    db: AsyncSession,
    cr_id: str,
    execution_result: dict,
) -> None:
    from app.models.change_request import ChangeRequest
    from app.models.recurring_job import RecurringJob

    cr_uuid = uuid.UUID(cr_id)

    artifact_refs = execution_result.get("artifact_refs")
    if artifact_refs:
        cr = await db.get(ChangeRequest, cr_uuid)
        if cr:
            cr.artifact_refs = artifact_refs
            await db.flush()

    result = await db.execute(
        select(BackupTarget)
        .join(RecurringJob, BackupTarget.recurring_job_id == RecurringJob.id)
        .where(RecurringJob.last_cr_id == cr_uuid)
    )
    bt = result.scalar_one_or_none()
    if bt is None:
        return

    bt.last_successful_at = datetime.now(tz=timezone.utc)
    bt.last_successful_backup_cr_id = cr_uuid
    bt.status = BackupTargetStatus.healthy
    await db.flush()
    logger.info(f"BackupTarget {bt.id} updated to healthy after CR {cr_id} completed")
