# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.notification_routing_rule import NotificationRoutingRule
from app.routers import current_user
from app.services.notification_routing_service import preview_routing_rules

router = APIRouter(prefix="/notification-rules", tags=["Notification Rules"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class NotificationRuleBase(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 100
    match_cr_types: list[str] | None = None
    match_severities: list[str] | None = None
    match_asset_tags: dict[str, str] | None = None
    match_connector_types: list[str] | None = None
    match_status: list[str] | None = None
    notify_user_ids: list[str] | None = None
    notify_role_ids: list[str] | None = None
    notify_channels: list[str] | None = None
    suppress_default: bool = False


class NotificationRuleCreate(NotificationRuleBase):
    pass


class NotificationRuleUpdate(NotificationRuleBase):
    pass


class NotificationRuleRead(NotificationRuleBase):
    model_config = {"from_attributes": True}
    id: str
    created_at: datetime
    created_by: str | None


class PreviewRequest(BaseModel):
    cr_payload: dict[str, Any]


class PreviewResponse(BaseModel):
    user_ids: list[str]
    channels: list[str]
    suppress_default: bool
    matched_rules: list[str]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotificationRuleRead])
async def list_rules(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRoutingRule).order_by(
            NotificationRoutingRule.priority.asc(),
            NotificationRoutingRule.created_at.asc(),
        )
    )
    return result.scalars().all()


@router.post("", response_model=NotificationRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: NotificationRuleCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = NotificationRoutingRule(
        **body.model_dump(),
        created_by=str(user.id),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=NotificationRuleRead)
async def update_rule(
    rule_id: str,
    body: NotificationRuleUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRoutingRule).where(NotificationRoutingRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    for field, value in body.model_dump().items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationRoutingRule).where(NotificationRoutingRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    await db.delete(rule)
    await db.commit()


@router.post("/preview", response_model=PreviewResponse)
async def preview_rules(
    body: PreviewRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run: given a sample CR payload dict, show which rules would fire."""
    result = await preview_routing_rules(db, body.cr_payload)
    return result
