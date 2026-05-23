import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.identity_profile import IdentityProfile, IdentityAccount
from app.models.user import User
from app.routers import current_user

router = APIRouter(prefix="/identity", tags=["Identity"])


def _account_to_dict(account: IdentityAccount) -> dict:
    return {
        "id": str(account.id),
        "organization_id": str(account.organization_id),
        "identity_profile_id": str(account.identity_profile_id),
        "connector_id": str(account.connector_id),
        "connector_type": account.connector_type,
        "external_id": account.external_id,
        "username": account.username,
        "email": account.email,
        "raw_attributes": account.raw_attributes,
        "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "is_stale": account.is_stale,
    }


def _profile_to_dict(profile: IdentityProfile) -> dict:
    return {
        "id": str(profile.id),
        "organization_id": str(profile.organization_id),
        "display_name": profile.display_name,
        "primary_email": profile.primary_email,
        "source_idp_connector_id": str(profile.source_idp_connector_id) if profile.source_idp_connector_id else None,
        "correlation_method": profile.correlation_method,
        "last_synced_at": profile.last_synced_at.isoformat() if profile.last_synced_at else None,
        "created_at": profile.created_at.isoformat(),
        "accounts": [_account_to_dict(a) for a in profile.accounts],
    }


@router.get("/profiles")
async def list_profiles(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IdentityProfile)
        .where(IdentityProfile.organization_id == user.organization_id)
        .options(selectinload(IdentityProfile.accounts))
    )
    profiles = result.scalars().all()
    return [_profile_to_dict(p) for p in profiles]


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IdentityProfile)
        .where(
            IdentityProfile.id == profile_id,
            IdentityProfile.organization_id == user.organization_id,
        )
        .options(selectinload(IdentityProfile.accounts))
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Identity profile not found")
    return _profile_to_dict(profile)


@router.post("/sync")
async def trigger_sync(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services import identity_sync_service
    try:
        stats = await identity_sync_service.sync_all(db, organization_id=user.organization_id)
        await db.commit()
        return {"status": "ok", "stats": stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
