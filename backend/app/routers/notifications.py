# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.user import User
from app.models.notification import Notification
from app.routers import current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    event_type: str
    resource_type: str | None
    resource_id: str | None
    message: str
    read: bool
    created_at: datetime


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Notification).where(
        Notification.recipient_user_id == user.id,
        Notification.organization_id == user.organization_id,
    ).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        q = q.where(Notification.read == False)  # noqa: E712
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{notification_id}/read", status_code=200)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.recipient_user_id == user.id)
        .values(read=True)
    )
    await db.commit()
    return {"marked_read": True}


@router.post("/read-all", status_code=200)
async def mark_all_read(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.recipient_user_id == user.id, Notification.read == False)  # noqa: E712
        .values(read=True)
    )
    await db.commit()
    return {"marked_read": True}
