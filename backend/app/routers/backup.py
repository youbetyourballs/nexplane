# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/routers/backup.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.backup_target import BackupTarget, BackupTargetStatus
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.user import User
from app.routers import current_user
from app.schemas.backup import (
    BackupTargetCreate, BackupTargetRead,
    BackupHistoryRead, RestoreCrCreate, BackupContextRead,
)
from app.services.backup_target_service import compute_status
from pydantic import BaseModel as PydanticBaseModel


class RestoreCrRead(PydanticBaseModel):
    id: str
    status: str
    title: str


router = APIRouter(tags=["Backup & Recovery"])


@router.get("/backup-targets", response_model=list[BackupTargetRead])
async def list_backup_targets(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupTarget)
        .where(BackupTarget.organization_id == user.organization_id)
        .order_by(BackupTarget.created_at.desc())
    )
    targets = result.scalars().all()
    for bt in targets:
        bt.status = compute_status(bt)
    return targets


@router.post("/backup-targets", response_model=BackupTargetRead, status_code=201)
async def create_backup_target(
    body: BackupTargetCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bt = BackupTarget(
        organization_id=user.organization_id,
        recurring_job_id=body.recurring_job_id,
        asset_id=body.asset_id,
        target_description=body.target_description,
        expected_cadence_hours=body.expected_cadence_hours,
        status=BackupTargetStatus.unprotected,
    )
    bt.status = compute_status(bt)
    db.add(bt)
    await db.commit()
    await db.refresh(bt)
    return bt


@router.get("/backup-targets/{target_id}", response_model=BackupTargetRead)
async def get_backup_target(
    target_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupTarget).where(
            BackupTarget.id == target_id,
            BackupTarget.organization_id == user.organization_id,
        )
    )
    bt = result.scalar_one_or_none()
    if not bt:
        raise HTTPException(status_code=404, detail="Backup target not found")
    bt.status = compute_status(bt)
    return bt


@router.get("/backup-history", response_model=list[BackupHistoryRead])
async def list_backup_history(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == user.organization_id,
            ChangeRequest.status == ChangeRequestStatus.completed,
            ChangeRequest.artifact_refs.isnot(None),
        )
        .order_by(ChangeRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.post("/restore-crs", response_model=RestoreCrRead, status_code=201)
async def create_restore_cr(
    body: RestoreCrCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(ChangeRequest, body.source_cr_id)
    if not source or source.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Source backup CR not found")
    if not source.artifact_refs:
        raise HTTPException(status_code=422, detail="Source CR has no artifact references")

    restore_cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"Restore: {body.target_description}",
        description=body.notes or f"Restore from backup CR {source.id}",
        change_type=ChangeType.ssm_command,
        risk_level=RiskLevel.high,
        desired_outcome={
            "restore_type": body.restore_type,
            "target_description": body.target_description,
            "source_cr_id": str(body.source_cr_id),
            "artifact_refs": source.artifact_refs,
        },
        target_asset_ids=source.target_asset_ids,
        status=ChangeRequestStatus.draft,
        source="restore",
    )
    db.add(restore_cr)
    await db.commit()
    await db.refresh(restore_cr)

    from app.services.change_plan_service import plan_cr, PlanBlockedError
    async with db.begin_nested():
        try:
            await plan_cr(db, restore_cr)
        except PlanBlockedError:
            restore_cr.status = ChangeRequestStatus.planned

    await db.commit()
    return {"id": str(restore_cr.id), "status": restore_cr.status, "title": restore_cr.title}


@router.get("/change-requests/{cr_id}/backup-context", response_model=BackupContextRead)
async def get_backup_context(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await db.get(ChangeRequest, cr_id)
    if not cr or cr.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Change request not found")

    actionable = {
        ChangeRequestStatus.planned,
        ChangeRequestStatus.awaiting_approval,
        ChangeRequestStatus.approved,
    }
    if cr.status not in actionable:
        return BackupContextRead(has_backup=False)

    if not cr.target_asset_ids:
        return BackupContextRead(has_backup=False)

    asset_uuids = [uuid.UUID(a) if isinstance(a, str) else a for a in cr.target_asset_ids]
    result = await db.execute(
        select(BackupTarget).where(
            BackupTarget.organization_id == user.organization_id,
            BackupTarget.asset_id.in_(asset_uuids),
        ).order_by(BackupTarget.last_successful_at.desc().nulls_last())
    )
    bt = result.scalars().first()

    if bt is None or bt.last_successful_backup_cr_id is None:
        return BackupContextRead(has_backup=False)

    status = compute_status(bt)
    return BackupContextRead(
        has_backup=True,
        last_successful_at=bt.last_successful_at,
        artifact=None,
        backup_cr_id=bt.last_successful_backup_cr_id,
        overdue=(status == BackupTargetStatus.overdue),
    )
