# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.routers import current_user
from app.models.user import User
from app.models.policy_baseline import DriftAlert

router = APIRouter(prefix="/drift-alerts", tags=["Drift"])


class DriftAlertRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    asset_id: str
    policy_type: str
    new_behaviors: list
    status: str
    detected_at: datetime


@router.get("", response_model=list[DriftAlertRead])
async def list_drift_alerts(
    status: str | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(DriftAlert).where(DriftAlert.organization_id == user.organization_id)
    if status:
        q = q.where(DriftAlert.status == status)
    result = await db.execute(q.order_by(DriftAlert.detected_at.desc()).limit(100))
    return result.scalars().all()


@router.post("/{alert_id}/acknowledge", status_code=200)
async def acknowledge_drift_alert(
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    alert = await db.get(DriftAlert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return {"acknowledged": True}
