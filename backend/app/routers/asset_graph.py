# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship
from app.routers import current_user
from app.services.asset_graph import get_neighbors

router = APIRouter(prefix="/assets", tags=["Asset Graph"])


class RelationshipCreate(BaseModel):
    target_asset_id: uuid.UUID
    relationship_type: str
    rel_metadata: dict = {}


def _rel_to_dict(rel: AssetRelationship) -> dict:
    return {
        "id": str(rel.id),
        "organization_id": str(rel.organization_id),
        "source_asset_id": str(rel.source_asset_id),
        "target_asset_id": str(rel.target_asset_id),
        "relationship_type": rel.relationship_type,
        "rel_metadata": rel.rel_metadata or {},
        "created_by": str(rel.created_by) if rel.created_by else None,
        "created_at": rel.created_at.isoformat() if rel.created_at else None,
    }


@router.get("/{asset_id}/relationships")
async def list_relationships(
    asset_id: uuid.UUID,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    stmt = select(AssetRelationship).where(
        AssetRelationship.organization_id == user.organization_id,
        or_(
            AssetRelationship.source_asset_id == asset_id,
            AssetRelationship.target_asset_id == asset_id,
        ),
    )
    result = await db.execute(stmt)
    rels = result.scalars().all()
    return [_rel_to_dict(r) for r in rels]


@router.post("/{asset_id}/relationships", status_code=201)
async def create_relationship(
    asset_id: uuid.UUID,
    body: RelationshipCreate,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(Asset, asset_id)
    if not source or source.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Source asset not found")

    target = await db.get(Asset, body.target_asset_id)
    if not target or target.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Target asset not found")

    existing = await db.execute(
        select(AssetRelationship).where(
            AssetRelationship.organization_id == user.organization_id,
            AssetRelationship.source_asset_id == asset_id,
            AssetRelationship.target_asset_id == body.target_asset_id,
            AssetRelationship.relationship_type == body.relationship_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Relationship already exists")

    rel = AssetRelationship(
        organization_id=user.organization_id,
        source_asset_id=asset_id,
        target_asset_id=body.target_asset_id,
        relationship_type=body.relationship_type,
        rel_metadata=body.rel_metadata,
        created_by=user.id,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return _rel_to_dict(rel)


@router.delete("/relationships/{rel_id}", status_code=204)
async def delete_relationship(
    rel_id: uuid.UUID,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    rel = await db.get(AssetRelationship, rel_id)
    if not rel or rel.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Relationship not found")
    await db.delete(rel)
    await db.commit()


@router.get("/{asset_id}/graph")
async def get_graph(
    asset_id: uuid.UUID,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    neighbors = await get_neighbors(db, asset_id, user.organization_id, direction="both")

    stmt = select(AssetRelationship).where(
        AssetRelationship.organization_id == user.organization_id,
        or_(
            AssetRelationship.source_asset_id == asset_id,
            AssetRelationship.target_asset_id == asset_id,
        ),
    )
    result = await db.execute(stmt)
    edges = [_rel_to_dict(r) for r in result.scalars().all()]

    return {
        "asset": {
            "id": str(asset.id),
            "name": asset.name,
            "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
            "environment": str(asset.environment.value) if hasattr(asset.environment, 'value') else str(asset.environment),
            "criticality": str(asset.criticality.value) if hasattr(asset.criticality, 'value') else str(asset.criticality),
        },
        "neighbors": neighbors,
        "edges": edges,
    }
