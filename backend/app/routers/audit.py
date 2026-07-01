# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_event import AuditEvent
from app.models.user import User
from app.routers import current_user
from app.schemas.audit_event import AuditEventRead

router = APIRouter(prefix="/audit-events", tags=["Audit"])


@router.get("", response_model=list[AuditEventRead])
async def list_audit_events(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(AuditEvent)
        .where(AuditEvent.organization_id == user.organization_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/change-requests/{cr_id}", response_model=list[AuditEventRead])
async def list_cr_audit_events(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == user.organization_id,
            AuditEvent.change_request_id == cr_id,
        )
        .order_by(AuditEvent.created_at.asc())
    )
    return result.scalars().all()
