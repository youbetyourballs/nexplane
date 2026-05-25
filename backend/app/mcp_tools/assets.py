"""
Nexplane MCP tools — Assets domain (5 tools).
"""
from __future__ import annotations
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_assets(
    token: str,
    asset_type: Optional[str] = None,
    environment: Optional[str] = None,
    criticality: Optional[str] = None,
    connector_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List assets for the org. Filter by asset_type (server/cloud_account/dns_zone/etc.),
    environment (dev/staging/prod), criticality (low/medium/high/critical), or connector_id.
    Returns summary fields — use get_asset for full detail.
    """
    from sqlalchemy import select
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(Asset).where(
            Asset.organization_id == user.organization_id
        ).order_by(Asset.created_at.desc()).limit(limit)

        if asset_type:
            stmt = stmt.where(Asset.asset_type == asset_type)
        if environment:
            stmt = stmt.where(Asset.environment == environment)
        if criticality:
            stmt = stmt.where(Asset.criticality == criticality)
        if connector_id:
            stmt = stmt.where(Asset.connector_id == _uuid.UUID(connector_id))

        result = await db.execute(stmt)
        assets = result.scalars().all()

        return [
            {
                "id": str(a.id),
                "name": a.name,
                "asset_type": str(a.asset_type),
                "environment": str(a.environment),
                "criticality": str(a.criticality),
                "connector_id": str(a.connector_id) if a.connector_id else None,
                "tags": a.tags,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in assets
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_asset(token: str, asset_id: str) -> dict[str, Any]:
    """
    Get an asset record including metadata, tags, and linked connector.
    """
    from sqlalchemy import select
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Asset).where(
                Asset.id == _uuid.UUID(asset_id),
                Asset.organization_id == user.organization_id,
            )
        )
        a = result.scalar_one_or_none()
        if a is None:
            return {"error": "Asset not found"}
        return {
            "id": str(a.id),
            "name": a.name,
            "asset_type": str(a.asset_type),
            "environment": str(a.environment),
            "criticality": str(a.criticality),
            "connector_id": str(a.connector_id) if a.connector_id else None,
            "tags": a.tags,
            "metadata": a.asset_metadata,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_asset_context(token: str, asset_id: str) -> dict[str, Any]:
    """
    Get a full planning context bundle for an asset: asset record, installed software (from last
    discovery), open findings, recent CRs (last 10), recent timeline events (last 20), connected
    connectors. Use this before creating a CR to ensure the plan is appropriate.
    """
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        ctx = await build_asset_context(_uuid.UUID(asset_id), db)
        if not ctx:
            return {"error": "Asset not found"}
        return ctx
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_asset_findings(
    token: str,
    asset_id: str,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List all findings for a specific asset with severity and status.
    Filter by status (open/actionable/remediating/etc.) or severity (critical/high/medium/low).
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        # Verify asset belongs to org
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id == _uuid.UUID(asset_id),
                Asset.organization_id == user.organization_id,
            )
        )
        if asset_result.scalar_one_or_none() is None:
            return [{"error": "Asset not found"}]

        stmt = select(VulnerabilityFinding).where(
            VulnerabilityFinding.asset_id == _uuid.UUID(asset_id)
        ).order_by(VulnerabilityFinding.created_at.desc()).limit(limit)

        if status:
            stmt = stmt.where(VulnerabilityFinding.status == status)
        if severity:
            stmt = stmt.where(VulnerabilityFinding.severity == severity)

        result = await db.execute(stmt)
        findings = result.scalars().all()

        return [
            {
                "id": str(f.id),
                "title": f.title,
                "cve_id": f.cve_id,
                "severity": str(f.severity),
                "status": str(f.status),
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in findings
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_asset_timeline(
    token: str,
    asset_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Get recent change and event history for an asset. Returns the last N timeline events
    including CR executions, finding ingests, and scan events.
    """
    from sqlalchemy import select
    from app.models.asset import Asset
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id == _uuid.UUID(asset_id),
                Asset.organization_id == user.organization_id,
            )
        )
        if asset_result.scalar_one_or_none() is None:
            return [{"error": "Asset not found"}]

        cr_result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.organization_id == user.organization_id,
            ).order_by(ChangeRequest.created_at.desc()).limit(limit)
        )
        crs = cr_result.scalars().all()
        asset_crs = [
            cr for cr in crs
            if asset_id in (cr.target_asset_ids or [])
        ]

        return [
            {
                "event_type": "change_request",
                "cr_id": str(cr.id),
                "change_type": str(cr.change_type),
                "status": str(cr.status),
                "timestamp": cr.created_at.isoformat() if cr.created_at else None,
            }
            for cr in asset_crs
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
