# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.change_request import ChangeRequest
from app.models.user import User
from app.routers import current_user
from app.services.asset_graph import get_upstream, get_downstream

router = APIRouter(prefix="/impact-simulation", tags=["Impact Simulation"])


@router.get("")
async def get_impact_simulation(
    asset_id: uuid.UUID = Query(..., description="Asset to analyse"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify asset belongs to org
    stmt = select(Asset).where(
        Asset.id == asset_id,
        Asset.organization_id == user.organization_id,
    )
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Traverse graph
    upstream = await get_upstream(db, asset_id, user.organization_id, max_depth=3)
    downstream = await get_downstream(db, asset_id, user.organization_id, max_depth=3)

    # Enrich downstream with criticality
    downstream_ids = [uuid.UUID(d["id"]) for d in downstream]
    criticality_map: dict[str, str] = {}
    if downstream_ids:
        crit_stmt = select(Asset.id, Asset.criticality).where(
            Asset.id.in_(downstream_ids),
            Asset.organization_id == user.organization_id,
        )
        crit_rows = await db.execute(crit_stmt)
        for row in crit_rows:
            criticality_map[str(row.id)] = (
                row.criticality.value if hasattr(row.criticality, "value") else str(row.criticality)
            )

    enriched_downstream = []
    for d in downstream:
        enriched_downstream.append({
            **d,
            "criticality": criticality_map.get(d["id"], "low"),
        })

    # Compute downstream risk counts
    critical_count = sum(1 for d in enriched_downstream if d["criticality"] == Criticality.critical.value)
    high_count = sum(1 for d in enriched_downstream if d["criticality"] == Criticality.high.value)
    downstream_risk = {
        "critical": critical_count,
        "high": high_count,
        "total": len(enriched_downstream),
    }

    # Recent CRs for the org (most recent 5)
    # target_asset_ids is a JSON column — cast to text for substring search is unreliable;
    # just return the 5 most recent CRs for the org as context.
    fallback_stmt = (
        select(ChangeRequest)
        .where(ChangeRequest.organization_id == user.organization_id)
        .order_by(ChangeRequest.created_at.desc())
        .limit(5)
    )
    fallback_result = await db.execute(fallback_stmt)
    crs = fallback_result.scalars().all()

    recent_crs = [
        {
            "id": str(cr.id),
            "title": cr.title,
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
        for cr in crs
    ]

    metadata = asset.asset_metadata or {}

    return {
        "asset": {
            "id": str(asset.id),
            "name": asset.name,
            "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
            "environment": asset.environment.value if hasattr(asset.environment, "value") else str(asset.environment),
            "criticality": asset.criticality.value if hasattr(asset.criticality, "value") else str(asset.criticality),
            "owner": metadata.get("owner"),
            "why_exists": metadata.get("why_exists"),
        },
        "upstream": upstream,
        "downstream": enriched_downstream,
        "downstream_risk": downstream_risk,
        "recent_crs": recent_crs,
        "open_findings_count": 0,
    }
