# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/routers/backup.py
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.backup_storage import BackupStorage, RecoveryToken
from app.models.backup_target import BackupTarget, BackupTargetStatus
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.user import User
from app.routers import current_user
from app.schemas.backup import (
    BackupContextRead,
    BackupHistoryRead,
    BackupStorageCreate,
    BackupStorageRead,
    BackupStorageUpdate,
    BackupTargetCreate,
    BackupTargetRead,
    BackupTargetUpdate,
    RecoveryTokenRead,
    RestoreCrCreate,
    StrategyRecommendation,
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
        backup_tier=body.backup_tier,
        capture_strategy=body.capture_strategy,
        storage_id=body.storage_id,
        status=BackupTargetStatus.unprotected,
    )
    bt.status = compute_status(bt)
    db.add(bt)
    await db.commit()
    await db.refresh(bt)
    return bt


# Backup-type → strategy mapping (mirrors frontend BACKUP_TYPES)
_STRATEGY_MAP = {
    "machine_image": {
        "capture_strategy": "ebs_snapshot",
        "backup_tier": "machine",
        "reason": "Creates an EBS snapshot and AMI — full machine restore to a new EC2 instance.",
        "alternatives": [
            {"capture_strategy": "mgn_replication", "label": "MGN continuous replication (coming soon)"},
            {"capture_strategy": "lvm_snapshot", "label": "LVM snapshot — Linux bare metal (coming soon)"},
            {"capture_strategy": "disk2vhd", "label": "Disk2vhd — Windows bare metal (coming soon)"},
        ],
    },
    "file_archive": {
        "capture_strategy": "local_files",
        "backup_tier": "data",
        "reason": "Archives a directory path via SSH/tar — lightweight file-level backup.",
        "alternatives": [
            {"capture_strategy": "nfs_files", "label": "NFS path archive (coming soon)"},
        ],
    },
    "database_dump": {
        "capture_strategy": "database_dump",
        "backup_tier": "data",
        "reason": "Runs pg_dump / mysqldump / mongodump against a self-hosted database.",
        "alternatives": [
            {"capture_strategy": "managed_db_snapshot", "label": "Managed DB snapshot — RDS/Cloud SQL (coming soon)"},
        ],
    },
    "storage_sync": {
        "capture_strategy": "storage_sync",
        "backup_tier": "data",
        "reason": "Syncs objects between storage backends (S3→GCS, NFS→S3, etc.).",
        "alternatives": [],
    },
}


@router.get("/backup-targets/recommend-strategy", response_model=StrategyRecommendation)
async def recommend_strategy(
    backup_type: str = Query(..., description="machine_image | file_archive | database_dump | storage_sync"),
    asset_id: uuid.UUID | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    mapping = _STRATEGY_MAP.get(backup_type)
    if not mapping:
        raise HTTPException(status_code=422, detail=f"Unknown backup_type: {backup_type}")
    return StrategyRecommendation(**mapping)


@router.patch("/backup-targets/{target_id}", response_model=BackupTargetRead)
async def patch_backup_target(
    target_id: uuid.UUID,
    body: BackupTargetUpdate,
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
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(bt, field, value)
    bt.status = compute_status(bt)
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


# ---------------------------------------------------------------------------
# BackupStorage CRUD
# ---------------------------------------------------------------------------


@router.post("/backup-storage", response_model=BackupStorageRead, status_code=201)
async def create_backup_storage(
    body: BackupStorageCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.is_org_default:
        existing = await db.execute(
            select(BackupStorage).where(
                BackupStorage.organization_id == user.organization_id,
                BackupStorage.is_org_default == True,
            )
        )
        for bs in existing.scalars().all():
            bs.is_org_default = False
        await db.flush()

    bs = BackupStorage(
        organization_id=user.organization_id,
        name=body.name,
        storage_type=body.storage_type,
        config=body.config,
        is_org_default=body.is_org_default,
    )
    db.add(bs)
    await db.commit()
    await db.refresh(bs)
    return BackupStorageRead(
        id=str(bs.id),
        name=bs.name,
        storage_type=bs.storage_type,
        is_org_default=bs.is_org_default,
        created_at=bs.created_at.isoformat(),
    )


@router.get("/backup-storage", response_model=list[BackupStorageRead])
async def list_backup_storage(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupStorage)
        .where(BackupStorage.organization_id == user.organization_id)
        .order_by(BackupStorage.is_org_default.desc(), BackupStorage.created_at.desc())
    )
    return [
        BackupStorageRead(
            id=str(bs.id),
            name=bs.name,
            storage_type=bs.storage_type,
            is_org_default=bs.is_org_default,
            created_at=bs.created_at.isoformat(),
        )
        for bs in result.scalars().all()
    ]


@router.get("/backup-storage/{storage_id}", response_model=BackupStorageRead)
async def get_backup_storage(
    storage_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    return BackupStorageRead(
        id=str(bs.id),
        name=bs.name,
        storage_type=bs.storage_type,
        is_org_default=bs.is_org_default,
        created_at=bs.created_at.isoformat(),
    )


@router.patch("/backup-storage/{storage_id}", response_model=BackupStorageRead)
async def update_backup_storage(
    storage_id: uuid.UUID,
    body: BackupStorageUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    if body.name is not None:
        bs.name = body.name
    if body.config is not None:
        bs.config = body.config
    if body.is_org_default is not None:
        if body.is_org_default:
            existing = await db.execute(
                select(BackupStorage).where(
                    BackupStorage.organization_id == user.organization_id,
                    BackupStorage.is_org_default == True,
                    BackupStorage.id != storage_id,
                )
            )
            for other in existing.scalars().all():
                other.is_org_default = False
        bs.is_org_default = body.is_org_default
    await db.commit()
    await db.refresh(bs)
    return BackupStorageRead(
        id=str(bs.id),
        name=bs.name,
        storage_type=bs.storage_type,
        is_org_default=bs.is_org_default,
        created_at=bs.created_at.isoformat(),
    )


@router.delete("/backup-storage/{storage_id}", status_code=204)
async def delete_backup_storage(
    storage_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    await db.delete(bs)
    await db.commit()


# ---------------------------------------------------------------------------
# Recovery token for bootstrap restore
# ---------------------------------------------------------------------------


@router.post("/backup-storage/{storage_id}/generate-recovery-token", response_model=RecoveryTokenRead, status_code=201)
async def generate_recovery_token(
    storage_id: uuid.UUID,
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")

    from app.models.asset import Asset
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)

    rt = RecoveryToken(
        organization_id=user.organization_id,
        token_hash=token_hash,
        asset_id=asset_id,
        expires_at=expires_at,
        used=False,
    )
    db.add(rt)
    await db.commit()
    await db.refresh(rt)
    return RecoveryTokenRead(
        token=raw,
        asset_id=str(asset_id),
        expires_at=expires_at.isoformat(),
        token_id=str(rt.id),
    )
