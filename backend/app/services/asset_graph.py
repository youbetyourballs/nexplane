"""
Graph traversal service for directed asset relationships.

Edge direction: source_asset_id --[type]--> target_asset_id
"source depends_on target" means source needs target to function.

get_upstream(asset_id):   what asset_id depends on (follow source→target outward)
get_downstream(asset_id): what depends on asset_id (follow target←source inward)
"""
import uuid
from collections import deque
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship


async def get_neighbors(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    direction: Literal["upstream", "downstream", "both"] = "both",
) -> list[dict]:
    """
    Immediate neighbors of asset_id within org_id.
    upstream:   assets that asset_id depends on (source==asset_id)
    downstream: assets that depend on asset_id (target==asset_id)
    """
    results: list[dict] = []

    if direction in ("upstream", "both"):
        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.target_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.source_asset_id == asset_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            results.append({
                "id": str(asset.id),
                "name": asset.name,
                "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
                "relationship_type": rel.relationship_type,
                "direction": "upstream",
                "rel_id": str(rel.id),
            })

    if direction in ("downstream", "both"):
        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.source_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.target_asset_id == asset_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            results.append({
                "id": str(asset.id),
                "name": asset.name,
                "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
                "relationship_type": rel.relationship_type,
                "direction": "downstream",
                "rel_id": str(rel.id),
            })

    return results


async def get_upstream(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    max_depth: int = 3,
) -> list[dict]:
    """BFS: all assets that asset_id transitively depends on."""
    visited: set[uuid.UUID] = {asset_id}
    queue: deque[tuple[uuid.UUID, int]] = deque([(asset_id, 0)])
    results: list[dict] = []

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.target_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.source_asset_id == current_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            if asset.id not in visited:
                visited.add(asset.id)
                results.append({
                    "id": str(asset.id),
                    "name": asset.name,
                    "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
                    "relationship_type": rel.relationship_type,
                    "direction": "upstream",
                    "depth": depth + 1,
                })
                queue.append((asset.id, depth + 1))

    return results


async def get_downstream(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    max_depth: int = 3,
) -> list[dict]:
    """BFS: all assets that transitively depend on asset_id."""
    visited: set[uuid.UUID] = {asset_id}
    queue: deque[tuple[uuid.UUID, int]] = deque([(asset_id, 0)])
    results: list[dict] = []

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.source_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.target_asset_id == current_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            if asset.id not in visited:
                visited.add(asset.id)
                results.append({
                    "id": str(asset.id),
                    "name": asset.name,
                    "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, 'value') else str(asset.asset_type),
                    "relationship_type": rel.relationship_type,
                    "direction": "downstream",
                    "depth": depth + 1,
                })
                queue.append((asset.id, depth + 1))

    return results
