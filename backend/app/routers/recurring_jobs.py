import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recurring_job import RecurringJob
from app.models.user import User
from app.routers import current_user
from app.schemas.recurring_job import RecurringJobCreate, RecurringJobUpdate, RecurringJobRead
from app.services.recurring_job_service import (
    register_job, deregister_job, fire_job_now, compute_next_run,
)

router = APIRouter(prefix="/recurring-jobs", tags=["Recurring Jobs"])


async def _get_job(db: AsyncSession, job_id: uuid.UUID, org_id: uuid.UUID) -> RecurringJob:
    result = await db.execute(
        select(RecurringJob).where(
            RecurringJob.id == job_id,
            RecurringJob.organization_id == org_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Recurring job not found")
    return job


@router.get("", response_model=list[RecurringJobRead])
async def list_jobs(
    job_type: str | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(RecurringJob).where(RecurringJob.organization_id == user.organization_id)
    if job_type:
        q = q.where(RecurringJob.job_type == job_type)
    result = await db.execute(q.order_by(RecurringJob.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=RecurringJobRead, status_code=201)
async def create_job(
    body: RecurringJobCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = RecurringJob(
        organization_id=user.organization_id,
        created_by=user.id,
        next_run_at=compute_next_run(body.cron_expression),
        **body.model_dump(),
    )
    db.add(job)
    await db.flush()
    register_job(job)
    if job.job_type.value == "backup":
        from app.services.backup_target_service import auto_create_for_job
        await auto_create_for_job(db, job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{job_id}", response_model=RecurringJobRead)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_job(db, job_id, user.organization_id)


@router.put("/{job_id}", response_model=RecurringJobRead)
async def update_job(
    job_id: uuid.UUID,
    body: RecurringJobUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(job, field, value)
    if body.cron_expression:
        job.next_run_at = compute_next_run(body.cron_expression)
    deregister_job(str(job.id))
    if job.enabled:
        register_job(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    deregister_job(str(job.id))
    await db.delete(job)
    await db.commit()


@router.post("/{job_id}/enable", response_model=RecurringJobRead)
async def enable_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    job.enabled = True
    job.next_run_at = compute_next_run(job.cron_expression)
    register_job(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/disable", response_model=RecurringJobRead)
async def disable_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    job.enabled = False
    deregister_job(str(job.id))
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/run-now", response_model=RecurringJobRead)
async def run_now(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    await fire_job_now(job)
    await db.refresh(job)
    return job
