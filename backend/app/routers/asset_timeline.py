# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.routers import current_user
from app.models.user import User
from app.models.change_request import ChangeRequest

router = APIRouter(prefix="/assets", tags=["Assets"])


class TimelineEvent(BaseModel):
    id: str
    timestamp: datetime
    event_type: str
    resource_type: str
    resource_id: str
    description: str
    actor_id: str | None
    outcome: str | None


@router.get("/{asset_id}/timeline", response_model=list[TimelineEvent])
async def asset_timeline(
    asset_id: uuid.UUID,
    limit: int = 100,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset_id_str = str(asset_id)
    events: list[TimelineEvent] = []

    # Change requests targeting this asset
    cr_result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.organization_id == user.organization_id,
        ).order_by(ChangeRequest.created_at.desc()).limit(limit * 5)
    )
    for cr in cr_result.scalars():
        # Filter by asset_id in Python (JSON array contains check)
        target_ids = cr.target_asset_ids or []
        if asset_id_str not in [str(t) for t in target_ids]:
            continue
        events.append(TimelineEvent(
            id=str(cr.id),
            timestamp=cr.created_at,
            event_type=f"cr.{cr.status.value}",
            resource_type="change_request",
            resource_id=str(cr.id),
            description=f"{cr.change_type.value}: {cr.title}",
            actor_id=str(cr.requester_id) if cr.requester_id else None,
            outcome=cr.status.value,
        ))

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]
