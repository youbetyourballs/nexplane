import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.identity_provider import IdentityProvider, IdpStatus
from app.models.user import User
from app.routers import current_user
from app.schemas.identity_provider import (
    IdentityProviderCreate,
    IdentityProviderUpdate,
    IdentityProviderRead,
)

router = APIRouter(prefix="/identity-providers", tags=["Identity Providers"])


# Public endpoint — no auth required (for pre-login "continue with" buttons)
@router.get("/active", response_model=list[IdentityProviderRead])
async def list_active_idps(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.status == IdpStatus.active,
            IdentityProvider.enabled == True,
        )
    )
    return result.scalars().all()


@router.post("", response_model=IdentityProviderRead, status_code=201)
async def create_idp(
    body: IdentityProviderCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = IdentityProvider(
        id=uuid.uuid4(),
        org_id=user.organization_id,
        type=body.type,
        name=body.name,
        config=body.config,
        connector_id=body.connector_id,
    )
    db.add(idp)
    await db.commit()
    await db.refresh(idp)
    return idp


@router.get("", response_model=list[IdentityProviderRead])
async def list_idps(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IdentityProvider).where(IdentityProvider.org_id == user.organization_id)
    )
    return result.scalars().all()


@router.get("/{idp_id}", response_model=IdentityProviderRead)
async def get_idp(
    idp_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_org_idp(db, idp_id, user.organization_id)


@router.put("/{idp_id}", response_model=IdentityProviderRead)
async def update_idp(
    idp_id: uuid.UUID,
    body: IdentityProviderUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_org_idp(db, idp_id, user.organization_id)
    if body.name is not None:
        idp.name = body.name
    if body.config is not None:
        idp.config = body.config
    if body.enabled is not None:
        idp.enabled = body.enabled
    if body.connector_id is not None:
        idp.connector_id = body.connector_id
    await db.commit()
    await db.refresh(idp)
    return idp


@router.delete("/{idp_id}", status_code=204)
async def delete_idp(
    idp_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_org_idp(db, idp_id, user.organization_id)
    await db.delete(idp)
    await db.commit()


async def _get_org_idp(db: AsyncSession, idp_id: uuid.UUID, org_id: uuid.UUID) -> IdentityProvider:
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.id == idp_id,
            IdentityProvider.org_id == org_id,
        )
    )
    idp = result.scalar_one_or_none()
    if not idp:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    return idp
