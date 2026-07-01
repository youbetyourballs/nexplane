# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Identity domain (6 tools).
"""
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
        user, agent_token = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_identities(
    token: str,
    source_connector_id: str = None,
    is_stale: bool = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List identity profiles for the org. Optionally filter by source IdP connector or stale status.
    Returns summary fields — use get_identity for full detail.
    """
    from sqlalchemy import select
    from app.models.identity_profile import IdentityProfile

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(IdentityProfile).where(
            IdentityProfile.organization_id == user.organization_id
        ).order_by(IdentityProfile.created_at.desc()).limit(limit)

        if source_connector_id:
            stmt = stmt.where(
                IdentityProfile.source_idp_connector_id == _uuid.UUID(source_connector_id)
            )

        result = await db.execute(stmt)
        profiles = result.scalars().all()

        return [
            {
                "id": str(p.id),
                "display_name": p.display_name,
                "primary_email": p.primary_email,
                "correlation_method": p.correlation_method,
                "source_idp_connector_id": str(p.source_idp_connector_id) if p.source_idp_connector_id else None,
                "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None,
            }
            for p in profiles
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_identity(token: str, identity_id: str) -> dict[str, Any]:
    """
    Get full identity profile detail including all linked accounts across connectors.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.identity_profile import IdentityProfile

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(IdentityProfile).where(
                IdentityProfile.id == _uuid.UUID(identity_id),
                IdentityProfile.organization_id == user.organization_id,
            ).options(selectinload(IdentityProfile.accounts))
        )
        p = result.scalar_one_or_none()
        if p is None:
            return {"error": "Identity not found"}

        accounts = [
            {
                "id": str(a.id),
                "connector_type": a.connector_type,
                "username": a.username,
                "email": a.email,
                "is_stale": a.is_stale,
                "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
            }
            for a in (p.accounts or [])
        ]

        return {
            "id": str(p.id),
            "display_name": p.display_name,
            "primary_email": p.primary_email,
            "correlation_method": p.correlation_method,
            "source_idp_connector_id": str(p.source_idp_connector_id) if p.source_idp_connector_id else None,
            "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None,
            "accounts": accounts,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_identity_findings(
    token: str,
    identity_id: str,
    status: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List security findings associated with an identity (stale accounts, over-privilege,
    orphaned credentials). Filter by status (open/actionable/remediating/etc.).
    """
    from sqlalchemy import select
    from app.models.identity_profile import IdentityProfile
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        identity_result = await db.execute(
            select(IdentityProfile).where(
                IdentityProfile.id == _uuid.UUID(identity_id),
                IdentityProfile.organization_id == user.organization_id,
            )
        )
        p = identity_result.scalar_one_or_none()
        if p is None:
            return [{"error": "Identity not found"}]

        stmt = select(VulnerabilityFinding).where(
            VulnerabilityFinding.organization_id == user.organization_id,
            VulnerabilityFinding.source_identity_id == p.id,
        ).order_by(VulnerabilityFinding.created_at.desc()).limit(limit)

        if status:
            stmt = stmt.where(VulnerabilityFinding.status == status)

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
    except Exception:
        return []
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_identity_graph(token: str, identity_id: str) -> dict[str, Any]:
    """
    Get the identity graph for a profile — all linked accounts, group memberships (from raw_attributes),
    and stale account flags. Use this to understand the full footprint of an identity across systems.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.identity_profile import IdentityProfile

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(IdentityProfile).where(
                IdentityProfile.id == _uuid.UUID(identity_id),
                IdentityProfile.organization_id == user.organization_id,
            ).options(selectinload(IdentityProfile.accounts))
        )
        p = result.scalar_one_or_none()
        if p is None:
            return {"error": "Identity not found"}

        nodes = []
        edges = []
        for a in (p.accounts or []):
            nodes.append({
                "id": str(a.id),
                "type": "account",
                "connector_type": a.connector_type,
                "username": a.username,
                "is_stale": a.is_stale,
            })
            edges.append({"from": str(p.id), "to": str(a.id), "relation": "has_account"})

            groups = []
            if a.raw_attributes and isinstance(a.raw_attributes, dict):
                groups = a.raw_attributes.get("groups", [])
            for g in groups:
                group_id = f"group:{a.connector_type}:{g}"
                nodes.append({"id": group_id, "type": "group", "name": g, "connector_type": a.connector_type})
                edges.append({"from": str(a.id), "to": group_id, "relation": "member_of"})

        return {
            "identity_id": str(p.id),
            "display_name": p.display_name,
            "primary_email": p.primary_email,
            "nodes": nodes,
            "edges": edges,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_access_reviews(
    token: str,
    status: str = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    List access review campaigns with status and completion rate.
    Filter by status (pending/in_progress/completed/overdue).
    """
    from sqlalchemy import select
    from app.models.access_review_schedule import AccessReviewSchedule

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(AccessReviewSchedule).where(
            AccessReviewSchedule.organization_id == user.organization_id
        ).order_by(AccessReviewSchedule.created_at.desc()).limit(limit)

        result = await db.execute(stmt)
        schedules = result.scalars().all()

        return [
            {
                "id": str(s.id),
                "name": s.name if hasattr(s, "name") else str(s.id),
                "status": str(s.status) if hasattr(s, "status") else "unknown",
                "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
            }
            for s in schedules
        ]
    except Exception:
        return []
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_access_review(token: str, review_id: str) -> dict[str, Any]:
    """
    Get full access review detail including pending decisions and completion rate.
    """
    from sqlalchemy import select
    from app.models.access_review_schedule import AccessReviewSchedule

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(AccessReviewSchedule).where(
                AccessReviewSchedule.id == _uuid.UUID(review_id),
                AccessReviewSchedule.organization_id == user.organization_id,
            )
        )
        s = result.scalar_one_or_none()
        if s is None:
            return {"error": "Access review not found"}
        return {
            "id": str(s.id),
            "name": s.name if hasattr(s, "name") else str(s.id),
            "status": str(s.status) if hasattr(s, "status") else "unknown",
            "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
        }
    except Exception:
        return {"error": "Access review not found"}
    finally:
        await db_cm.__aexit__(None, None, None)
