from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, cast, String, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.user import User
from app.routers import current_user

router = APIRouter(prefix="/infrastructure-memory", tags=["Infrastructure Memory"])

_CRITICALITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


@router.get("")
async def list_infrastructure_memory(
    q: str | None = Query(None, description="Search name, owner, why_exists"),
    owner: str | None = Query(None, description="Filter by owner (case-insensitive)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).where(Asset.organization_id == user.organization_id)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Asset.name.ilike(pattern),
                cast(Asset.asset_metadata["owner"].astext, String).ilike(pattern),
                cast(Asset.asset_metadata["why_exists"].astext, String).ilike(pattern),
            )
        )

    if owner:
        stmt = stmt.where(
            cast(Asset.asset_metadata["owner"].astext, String).ilike(owner)
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.order_by(Asset.name)
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    result = await db.execute(stmt)
    assets = result.scalars().all()

    assets_list = sorted(
        assets,
        key=lambda a: (_CRITICALITY_ORDER.get(a.criticality.value if hasattr(a.criticality, 'value') else str(a.criticality), 99), a.name)
    )

    items = [
        {
            "id": str(a.id),
            "name": a.name,
            "asset_type": a.asset_type.value if hasattr(a.asset_type, 'value') else str(a.asset_type),
            "environment": a.environment.value if hasattr(a.environment, 'value') else str(a.environment),
            "criticality": a.criticality.value if hasattr(a.criticality, 'value') else str(a.criticality),
            "owner": (a.asset_metadata or {}).get("owner"),
            "why_exists": (a.asset_metadata or {}).get("why_exists"),
        }
        for a in assets_list
    ]

    return {"total": total, "page": page, "page_size": page_size, "items": items}
