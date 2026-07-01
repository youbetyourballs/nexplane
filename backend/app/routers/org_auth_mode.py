# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.organization import Organization, OrgAuthMode
from app.models.identity_provider import IdentityProvider, IdpStatus
from app.models.user import User
from app.routers import current_user
from app.schemas.identity_provider import AuthModeSwitch

router = APIRouter(prefix="/orgs", tags=["Organizations"])


@router.post("/{org_id}/auth-mode")
async def set_auth_mode(
    org_id: uuid.UUID,
    body: AuthModeSwitch,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if str(org_id) != str(user.organization_id):
        raise HTTPException(status_code=403, detail="Cannot modify a different org")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    if body.auth_mode == "idp":
        if not body.idp_id:
            raise HTTPException(status_code=422, detail="idp_id is required when switching to idp mode")
        idp_result = await db.execute(
            select(IdentityProvider).where(
                IdentityProvider.id == body.idp_id,
                IdentityProvider.org_id == org_id,
            )
        )
        idp = idp_result.scalar_one_or_none()
        if not idp:
            raise HTTPException(status_code=404, detail="Identity provider not found")
        if not idp.enabled:
            raise HTTPException(status_code=400, detail="Identity provider must be enabled before activating")
        org.auth_mode = OrgAuthMode.idp
        org.auth_mode_changed_at = datetime.now(timezone.utc)
        idp.status = IdpStatus.active

    elif body.auth_mode == "local":
        org.auth_mode = OrgAuthMode.local
        org.auth_mode_changed_at = datetime.now(timezone.utc)
        active_result = await db.execute(
            select(IdentityProvider).where(
                IdentityProvider.org_id == org_id,
                IdentityProvider.status == IdpStatus.active,
            )
        )
        for idp in active_result.scalars().all():
            idp.status = IdpStatus.pending
    else:
        raise HTTPException(status_code=422, detail="auth_mode must be 'local' or 'idp'")

    await db.commit()
    await db.refresh(org)
    return {"auth_mode": org.auth_mode, "auth_mode_changed_at": org.auth_mode_changed_at}
