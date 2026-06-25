"""
GET /recommendations

Returns rule-based recommendations for the authenticated org.
Rules applied against assets and asset_relationships — no new tables.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.asset_relationship import AssetRelationship
from app.models.user import User
from app.routers import current_user

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("")
async def list_recommendations(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = user.organization_id

    # Load all assets for org
    assets_result = await db.execute(
        select(Asset).where(Asset.organization_id == org_id)
    )
    assets = assets_result.scalars().all()

    # Load relationship counts: source and target per asset
    source_counts_result = await db.execute(
        select(AssetRelationship.source_asset_id, func.count().label("cnt"))
        .where(AssetRelationship.organization_id == org_id)
        .group_by(AssetRelationship.source_asset_id)
    )
    source_counts = {str(row.source_asset_id): row.cnt for row in source_counts_result}

    target_counts_result = await db.execute(
        select(AssetRelationship.target_asset_id, func.count().label("cnt"))
        .where(AssetRelationship.organization_id == org_id)
        .group_by(AssetRelationship.target_asset_id)
    )
    target_counts = {str(row.target_asset_id): row.cnt for row in target_counts_result}

    recs: dict[str, dict] = {}  # asset_id -> best recommendation

    def crit_val(asset) -> str:
        return asset.criticality.value if hasattr(asset.criticality, 'value') else str(asset.criticality)

    def type_val(asset) -> str:
        return asset.asset_type.value if hasattr(asset.asset_type, 'value') else str(asset.asset_type)

    def emit(asset, rule: str, title: str, description: str, priority: str):
        asset_id = str(asset.id)
        existing = recs.get(asset_id)
        if existing is None or PRIORITY_ORDER[priority] < PRIORITY_ORDER[existing["priority"]]:
            recs[asset_id] = {
                "id": f"{rule}:{asset_id}",
                "asset_id": asset_id,
                "asset_name": asset.name,
                "asset_type": type_val(asset),
                "criticality": crit_val(asset),
                "rule": rule,
                "title": title,
                "description": description,
                "priority": priority,
                "action_link": f"/assets/{asset_id}",
            }

    for asset in assets:
        meta = asset.asset_metadata or {}
        asset_id = str(asset.id)
        criticality = crit_val(asset)
        has_owner = bool(meta.get("owner"))
        has_why = bool(meta.get("why_exists"))
        total_edges = source_counts.get(asset_id, 0) + target_counts.get(asset_id, 0)
        downstream_count = target_counts.get(asset_id, 0)

        if not has_owner and criticality == "critical":
            emit(asset, "critical_missing_owner",
                 f"Assign an owner to critical asset",
                 f"{asset.name} is critical but has no assigned owner. Unowned critical assets create accountability gaps during incidents.",
                 "critical")

        elif not has_owner:
            emit(asset, "missing_owner",
                 "Assign an owner",
                 f"{asset.name} has no assigned owner. Every asset should have a team or person responsible for it.",
                 "medium")

        if criticality == "critical" and total_edges == 0:
            emit(asset, "critical_no_relationships",
                 "Map relationships for critical asset",
                 f"{asset.name} is critical but has no mapped dependencies or dependents. Its blast radius is unknown.",
                 "high")

        if downstream_count >= 5:
            emit(asset, "high_downstream_count",
                 "High blast radius — create a change plan",
                 f"{asset.name} has {downstream_count} downstream dependents. Changes to it require careful planning.",
                 "high")

        if not has_why:
            emit(asset, "missing_why_exists",
                 "Document why this asset exists",
                 f"{asset.name} has no documented reason for existing. This makes it harder to assess safe-to-remove candidates.",
                 "low")

    result = sorted(
        recs.values(),
        key=lambda r: (PRIORITY_ORDER[r["priority"]], r["asset_name"])
    )
    return result[:50]
