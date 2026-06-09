import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.database import get_db
from app.models.setup_token import SetupToken
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.dependencies.ops_secret import require_ops_secret
from app.schemas.setup_token import (
    SetupConsumeRequest,
    SetupConsumeResponse,
    SetupTokenCreateRequest,
    SetupTokenCreateResponse,
)
from app.services.auth_service import create_access_token, hash_password

router = APIRouter(prefix="/setup", tags=["Setup"])
_SETUP_TOKEN_TTL_HOURS = 24


def _require_commercial():
    if settings.NEXPLANE_EDITION != "commercial":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/consume", response_model=SetupConsumeResponse)
async def consume_setup_token(
    body: SetupConsumeRequest,
    db: AsyncSession = Depends(get_db),
):
    _require_commercial()

    if len(body.admin_password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    result = await db.execute(select(SetupToken).where(SetupToken.token_hash == token_hash))
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid token")
    if record.used_at is not None:
        raise HTTPException(status_code=400, detail="Token has already been used")
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token has expired")
    if record.instance_url.rstrip("/") != body.instance_url.rstrip("/"):
        raise HTTPException(status_code=400, detail="Token is not valid for this instance URL")

    org = Organization(id=uuid.uuid4(), name=body.org_name)
    db.add(org)
    await db.flush()

    admin = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=body.admin_email,
        name=body.admin_name,
        hashed_password=hash_password(body.admin_password),
        role=UserRole.admin,
    )
    db.add(admin)

    record.used_at = datetime.now(timezone.utc)
    record.org_id = org.id

    await db.commit()

    access_token = create_access_token(str(admin.id))
    return SetupConsumeResponse(
        access_token=access_token,
        user_id=admin.id,
        org_id=org.id,
    )


@router.post("/token", response_model=SetupTokenCreateResponse, status_code=201,
             dependencies=[Depends(require_ops_secret)])
async def create_setup_token(
    body: SetupTokenCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    _require_commercial()

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_SETUP_TOKEN_TTL_HOURS)

    record = SetupToken(
        id=uuid.uuid4(),
        token_hash=token_hash,
        instance_url=body.instance_url.rstrip("/"),
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()

    setup_url = f"{body.instance_url.rstrip('/')}/setup?token={raw}"
    return SetupTokenCreateResponse(token=raw, setup_url=setup_url, expires_at=expires_at)
