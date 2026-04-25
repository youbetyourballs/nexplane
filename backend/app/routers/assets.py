import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.user import User
from app.routers import current_user
from app.schemas.asset import AssetCreate, AssetRead
from app.services.audit_service import record_event

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.get("", response_model=list[AssetRead])
async def list_assets(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Asset).where(Asset.organization_id == user.organization_id))
    return result.scalars().all()


@router.post("", response_model=AssetRead, status_code=201)
async def create_asset(
    body: AssetCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = Asset(organization_id=user.organization_id, **body.model_dump())
    db.add(asset)
    await db.flush()
    await record_event(db, user.organization_id, "asset.created",
                       {"asset_id": str(asset.id), "name": asset.name}, actor_id=user.id)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
