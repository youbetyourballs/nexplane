import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.asset import Asset, Environment, AssetType, Criticality
from app.models.user import User
from app.routers import current_user
from app.schemas.asset import AssetCreate, AssetRead, AssetUpdate, BulkTagOperation
from app.services.audit_service import record_event

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.get("", response_model=list[AssetRead])
async def list_assets(
    q: str | None = Query(None, description="Asset name substring search"),
    env: str | None = Query(None, description="Filter by environment"),
    asset_type: str | None = Query(None, description="Filter by asset type"),
    criticality: str | None = Query(None, description="Filter by criticality"),
    tag: str | None = Query(None, description="Filter by tag (asset must have this tag)"),
    connector_id: uuid.UUID | None = Query(None, description="Filter by connector that discovered this asset"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).options(selectinload(Asset.connector)).where(Asset.organization_id == user.organization_id)

    if q:
        stmt = stmt.where(Asset.name.ilike(f"%{q}%"))
    if env:
        try:
            stmt = stmt.where(Asset.environment == Environment(env))
        except ValueError:
            pass
    if asset_type:
        try:
            stmt = stmt.where(Asset.asset_type == AssetType(asset_type))
        except ValueError:
            pass
    if criticality:
        try:
            stmt = stmt.where(Asset.criticality == Criticality(criticality))
        except ValueError:
            pass
    if connector_id:
        stmt = stmt.where(Asset.connector_id == connector_id)

    result = await db.execute(stmt.order_by(Asset.name))
    assets = result.scalars().all()

    # Tag filter applied in Python (JSON array containment)
    if tag:
        assets = [a for a in assets if tag in (a.tags or [])]

    return [
        AssetRead(
            **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
            connector_name=asset.connector.name if asset.connector else None,
        )
        for asset in assets
    ]


@router.get("/tags", response_model=list[str])
async def get_asset_tags(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all unique tags used across the org's assets, sorted alphabetically."""
    result = await db.execute(
        select(Asset.tags).where(Asset.organization_id == user.organization_id)
    )
    all_tags: set[str] = set()
    for (tags,) in result:
        if tags:
            all_tags.update(tags)
    return sorted(all_tags)


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


@router.patch("/bulk-tag")
async def bulk_tag_assets(
    body: BulkTagOperation,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_(body.asset_ids),
            Asset.organization_id == user.organization_id,
        )
    )
    assets = result.scalars().all()

    if len(assets) != len(body.asset_ids):
        raise HTTPException(status_code=403, detail="One or more assets not found or not accessible")

    for asset in assets:
        current_tags: list[str] = asset.tags or []
        if body.operation == "add":
            new_tags = list(dict.fromkeys(current_tags + body.tags))
        elif body.operation == "remove":
            new_tags = [t for t in current_tags if t not in body.tags]
        else:  # "set"
            new_tags = list(body.tags)
        asset.tags = new_tags

    await record_event(
        db, user.organization_id, "asset.bulk_tagged",
        {"operation": body.operation, "tags": body.tags, "asset_count": len(assets)},
        actor_id=user.id,
    )
    await db.commit()
    return {"updated": len(assets)}


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(
            Asset.id == asset_id,
            Asset.organization_id == user.organization_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetRead(
        **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
        connector_name=asset.connector.name if asset.connector else None,
    )


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    await record_event(db, user.organization_id, "asset.deleted",
                       {"asset_id": str(asset.id), "name": asset.name}, actor_id=user.id)
    await db.delete(asset)
    await db.commit()


@router.patch("/{asset_id}", response_model=AssetRead)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    if body.name is not None:
        asset.name = body.name
    if body.criticality is not None:
        asset.criticality = body.criticality
    if body.asset_metadata is not None:
        asset.asset_metadata = body.asset_metadata
    if body.tags is not None:
        asset.tags = body.tags

    await record_event(db, user.organization_id, "asset.updated",
                       {"asset_id": str(asset.id), "changes": body.model_dump(exclude_none=True)},
                       actor_id=user.id)
    await db.commit()
    await db.refresh(asset)
    return asset
