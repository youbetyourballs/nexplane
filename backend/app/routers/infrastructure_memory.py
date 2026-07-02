# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Infrastructure Memory Router

Endpoints that answer provenance questions about the live infrastructure.
All queries run against real DB records — no mocks, no simulation.

Supported website example queries:
  1. "Why is port 8443 open on prod-gateway-01?"
     → GET /memory/provenance/{asset_id}?config_key=8443
  2. "Who approved the firewall rule allowing 10.2.0.0/16?"
     → GET /memory/provenance/{asset_id}?config_key=10.2.0.0%2F16
     → POST /memory/query  {"query": "Who approved ..."}
  3. "Which applications depend on cert wildcard.acme.internal?"
     → GET /memory/dependencies/{asset_id}
  4. "Can prod-worker-07 be deleted?"
     → GET /memory/deletion-check/{asset_id}
  5. "What changed in the us-east-1 VPC yesterday?"
     → GET /memory/timeline?region=us-east-1&since=...
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, cast, String, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.user import User
from app.routers import current_user
from app.services.infrastructure_memory_service import (
    get_asset_provenance,
    search_crs_by_parameter,
    get_asset_dependents,
    can_asset_be_deleted,
    get_timeline_summary,
    parse_query_intent,
)

router = APIRouter(prefix="/memory", tags=["Infrastructure Memory"])

# ---------------------------------------------------------------------------
# Legacy endpoint — keep the old path working
# ---------------------------------------------------------------------------

_legacy_router = APIRouter(prefix="/infrastructure-memory", tags=["Infrastructure Memory"])

_CRITICALITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


@_legacy_router.get("")
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
                cast(Asset.asset_metadata["owner"], String).ilike(pattern),
                cast(Asset.asset_metadata["why_exists"], String).ilike(pattern),
            )
        )

    if owner:
        stmt = stmt.where(
            cast(Asset.asset_metadata["owner"], String).ilike(owner)
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
        key=lambda a: (
            _CRITICALITY_ORDER.get(
                a.criticality.value if hasattr(a.criticality, "value") else str(a.criticality), 99
            ),
            a.name,
        ),
    )

    items = [
        {
            "id": str(a.id),
            "name": a.name,
            "asset_type": a.asset_type.value if hasattr(a.asset_type, "value") else str(a.asset_type),
            "environment": a.environment.value if hasattr(a.environment, "value") else str(a.environment),
            "criticality": a.criticality.value if hasattr(a.criticality, "value") else str(a.criticality),
            "owner": (a.asset_metadata or {}).get("owner"),
            "why_exists": (a.asset_metadata or {}).get("why_exists"),
        }
        for a in assets_list
    ]

    return {"total": total, "page": page, "page_size": page_size, "items": items}


# ---------------------------------------------------------------------------
# 1 & 2. Provenance — why does this asset/config exist?
# ---------------------------------------------------------------------------

@router.get("/provenance/{asset_id}", summary="Why does this asset or config value exist?")
async def get_provenance(
    asset_id: uuid.UUID,
    config_key: Optional[str] = Query(
        None,
        description="Optional keyword to filter CR history (e.g. a port number, CIDR, rule name)",
    ),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the provenance of an asset: owner, why_exists, and the full CR history
    explaining why it was created / modified.  Optionally filter history by a keyword
    (useful for: "why is port 8443 open?" → config_key=8443).
    """
    result = await get_asset_provenance(
        db=db,
        org_id=user.organization_id,
        asset_id=asset_id,
        config_key=config_key,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# 3. Dependencies — what depends on this asset?
# ---------------------------------------------------------------------------

@router.get("/dependencies/{asset_id}", summary="What depends on this asset?")
async def get_dependencies(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all assets that depend on the given asset, plus cert expiry and owner metadata.

    Used for: "which applications depend on cert wildcard.acme.internal?"
    """
    result = await get_asset_dependents(
        db=db,
        org_id=user.organization_id,
        asset_id=asset_id,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# 4. Deletion check — can this asset be safely deleted?
# ---------------------------------------------------------------------------

@router.get("/deletion-check/{asset_id}", summary="Can this asset be safely deleted?")
async def deletion_check(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Safety analysis for deleting an asset.

    Returns: safe bool, dependent_count, last_deployed_at, has_rollback_snapshot,
             requires_approval, blocking_reasons.
    """
    result = await can_asset_be_deleted(
        db=db,
        org_id=user.organization_id,
        asset_id=asset_id,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# 5. Timeline — what changed in a scope/time window?
# ---------------------------------------------------------------------------

@router.get("/timeline", summary="What changed in a given scope and time window?")
async def timeline(
    asset_id: Optional[uuid.UUID] = Query(None, description="Scope to a specific asset"),
    tag: Optional[str] = Query(None, description="Filter by tag or keyword in CR text"),
    region: Optional[str] = Query(None, description="Filter by AWS/GCP/Azure region name"),
    since: Optional[datetime] = Query(
        None,
        description="Start of time window (ISO 8601). Defaults to 24h ago if omitted.",
    ),
    until: Optional[datetime] = Query(None, description="End of time window (ISO 8601)"),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Summarise infrastructure changes in the given time window and scope.

    Used for: "what changed in the us-east-1 VPC yesterday?"
    """
    if since is None and until is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    return await get_timeline_summary(
        db=db,
        org_id=user.organization_id,
        asset_id=asset_id,
        tag_filter=tag,
        region=region,
        since=since,
        until=until,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# POST /memory/query — natural-language dispatcher
# ---------------------------------------------------------------------------

class MemoryQuery(BaseModel):
    query: str
    asset_id: Optional[str] = None  # hint: pre-resolved asset UUID if caller knows it
    since_hours: Optional[int] = None  # shorthand time window for timeline queries


@router.post("/query", summary="Natural-language infrastructure memory query")
async def memory_query(
    body: MemoryQuery,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a natural-language question about the infrastructure and dispatch to the
    appropriate provenance function.

    Supported patterns:
      - "Why is port 8443 open on prod-gateway-01?"
      - "Who approved the firewall rule allowing 10.2.0.0/16?"
      - "Which applications depend on cert wildcard.acme.internal?"
      - "Can prod-worker-07 be deleted?"
      - "What changed in the us-east-1 VPC yesterday?"
    """
    intent_result = parse_query_intent(body.query)
    intent = intent_result["intent"]
    entities = intent_result["entities"]

    # If caller supplied an asset_id hint, use it
    resolved_asset_id: Optional[uuid.UUID] = None
    if body.asset_id:
        try:
            resolved_asset_id = uuid.UUID(body.asset_id)
        except ValueError:
            pass

    # If no asset_id hint, try to extract asset name from query and resolve
    if resolved_asset_id is None:
        asset_name_guess = _extract_asset_name(body.query)
        if asset_name_guess:
            resolved_asset_id = await _resolve_asset_by_name(
                db, user.organization_id, asset_name_guess
            )

    if intent == "deletion_check":
        if resolved_asset_id is None:
            return {
                "intent": intent,
                "error": "Could not resolve asset from query. Provide asset_id.",
                "query": body.query,
            }
        result = await can_asset_be_deleted(db, user.organization_id, resolved_asset_id)
        return {"intent": intent, "query": body.query, "result": result}

    elif intent == "timeline":
        since_dt: Optional[datetime] = None
        if "yesterday" in body.query.lower():
            now = datetime.now(timezone.utc)
            since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            until_dt = since_dt + timedelta(days=1)
        elif body.since_hours:
            since_dt = datetime.now(timezone.utc) - timedelta(hours=body.since_hours)
            until_dt = None
        else:
            since_dt = datetime.now(timezone.utc) - timedelta(hours=24)
            until_dt = None

        result = await get_timeline_summary(
            db=db,
            org_id=user.organization_id,
            asset_id=resolved_asset_id,
            region=entities.get("region"),
            since=since_dt,
            until=until_dt if "yesterday" in body.query.lower() else None,
        )
        return {"intent": intent, "query": body.query, "result": result}

    elif intent == "approval_search":
        search_val = entities.get("cidr") or entities.get("port") or body.query
        results = await search_crs_by_parameter(
            db=db,
            org_id=user.organization_id,
            parameter_value=search_val,
        )
        return {"intent": intent, "query": body.query, "results": results}

    elif intent == "dependents":
        if resolved_asset_id is None:
            return {
                "intent": intent,
                "error": "Could not resolve asset from query. Provide asset_id.",
                "query": body.query,
            }
        result = await get_asset_dependents(db, user.organization_id, resolved_asset_id)
        return {"intent": intent, "query": body.query, "result": result}

    else:  # provenance
        if resolved_asset_id is None:
            return {
                "intent": intent,
                "error": "Could not resolve asset from query. Provide asset_id.",
                "query": body.query,
            }
        config_key = entities.get("port") or entities.get("cidr")
        result = await get_asset_provenance(
            db=db,
            org_id=user.organization_id,
            asset_id=resolved_asset_id,
            config_key=config_key,
        )
        return {"intent": intent, "query": body.query, "result": result}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_asset_name(query: str) -> Optional[str]:
    """Very light heuristic: return the last token that looks like a hostname."""
    import re
    # Matches tokens like prod-gateway-01, prod-worker-07, wildcard.acme.internal
    matches = re.findall(r"[\w*][\w.*-]{3,}", query)
    # Filter out common stop words and short tokens
    stop = {"open", "port", "what", "changed", "yesterday", "approved", "firewall",
            "rule", "allowing", "depend", "deleted", "safe", "can", "which",
            "applications", "cert", "certificate", "who", "why", "does", "did"}
    candidates = [m for m in matches if m.lower() not in stop and len(m) > 4]
    return candidates[-1] if candidates else None


async def _resolve_asset_by_name(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str,
) -> Optional[uuid.UUID]:
    """Try exact then ILIKE match on asset name within org."""
    # Exact match first
    stmt = select(Asset.id).where(
        Asset.organization_id == org_id,
        Asset.name == name,
    ).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row:
        return row

    # ILIKE fallback
    stmt = select(Asset.id).where(
        Asset.organization_id == org_id,
        Asset.name.ilike(f"%{name}%"),
    ).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    return row
