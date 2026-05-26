import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers import current_user, require_roles
from app.schemas.auth import LoginRequest, Token, UserRead
from app.services.auth_service import authenticate_user, create_access_token
from app.models.user import User, UserRole
from app.models.agent_token import AgentToken
from app.schemas.agent_token import AgentTokenCreate, AgentTokenCreateResponse, AgentTokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

_require_admin = require_roles(UserRole.admin)


@router.post("/login", response_model=Token)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.email, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(str(user.id))
    return Token(access_token=token)


@router.post("/logout")
async def logout():
    return {"message": "Logged out"}


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(current_user)):
    return user


# ---------------------------------------------------------------------------
# Agent token helpers
# ---------------------------------------------------------------------------

async def _create_agent_token_in_db(db: AsyncSession, user: User, payload: AgentTokenCreate):
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at: Optional[datetime] = None
    if payload.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
    record = AgentToken(
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        name=payload.name,
        token_hash=token_hash,
        expires_at=expires_at,
        allowed_connector_types=payload.allowed_connector_types,
        allowed_asset_tags=payload.allowed_asset_tags,
        allowed_cr_types=payload.allowed_cr_types,
        allowed_roles=payload.allowed_roles,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record, raw


@router.post("/agent-tokens", response_model=AgentTokenCreateResponse, status_code=201)
async def create_agent_token(
    payload: AgentTokenCreate,
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    record, raw_token = await _create_agent_token_in_db(db, current_admin, payload)
    data = AgentTokenResponse.model_validate(record).model_dump()
    return AgentTokenCreateResponse(token=raw_token, **data)


@router.get("/agent-tokens", response_model=list[AgentTokenResponse])
async def list_agent_tokens(
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(AgentToken).where(AgentToken.organization_id == current_admin.organization_id)
    )
    return result.scalars().all()


@router.delete("/agent-tokens/{token_id}", status_code=204)
async def revoke_agent_token(
    token_id: uuid.UUID,
    current_admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.id == token_id,
            AgentToken.organization_id == current_admin.organization_id,
        )
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Agent token not found")
    token.revoked = True
    await db.commit()
