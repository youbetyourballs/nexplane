# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_token import ApiToken, _generate_raw_token, _hash_token
from app.models.user import User
from app.routers import current_user
from app.schemas.api_token import TokenCreate, TokenRead, TokenCreatedResponse

router = APIRouter(prefix="/api/v1/tokens", tags=["API Tokens"])


@router.post("", response_model=TokenCreatedResponse, status_code=201)
async def generate_token(
    body: TokenCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    raw = _generate_raw_token()
    token = ApiToken(
        organization_id=user.organization_id,
        user_id=user.id,
        name=body.name,
        token_hash=_hash_token(raw),
        expires_at=body.expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return TokenCreatedResponse(
        id=token.id,
        name=token.name,
        raw_token=raw,
        expires_at=token.expires_at,
        created_at=token.created_at,
    )


@router.get("", response_model=list[TokenRead])
async def list_tokens(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.organization_id == user.organization_id,
            ApiToken.revoked == False,
        ).order_by(ApiToken.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.organization_id == user.organization_id,
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(404, "Token not found")
    token.revoked = True
    await db.commit()
