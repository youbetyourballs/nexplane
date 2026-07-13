# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""MCP tools — Migration domain (application profiles, database instances)."""
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
async def list_application_profiles(
    token: str,
    environment: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List discovered application profiles. Each profile describes a running workload's
    endpoints, dependencies, services, config files, and library versions.
    Use to find what applications have been discovered before planning a migration.
    Filter by environment (dev/staging/prod).
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Asset)
            .where(
                Asset.organization_id == user.organization_id,
                Asset.asset_type == AssetType.application_profile,
            )
            .order_by(Asset.created_at.desc())
            .limit(limit)
        )
        if environment:
            from app.models.asset import Environment
            stmt = stmt.where(Asset.environment == environment)

        result = await db.execute(stmt)
        assets = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "environment": a.environment.value,
                "criticality": a.criticality.value,
                "tags": a.tags,
                "endpoint_count": len(a.asset_metadata.get("endpoints", [])),
                "dependency_count": len(a.asset_metadata.get("dependencies", [])),
                "created_at": a.created_at.isoformat(),
            }
            for a in assets
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_application_profile(
    token: str,
    asset_id: str,
) -> dict[str, Any]:
    """
    Get the full application profile for a discovered workload — endpoints, dependencies,
    config files, library versions. Use before planning a migration to understand what
    the application depends on and what verification targets to use.
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(Asset).where(
            Asset.id == _uuid.UUID(asset_id),
            Asset.organization_id == user.organization_id,
            Asset.asset_type == AssetType.application_profile,
        )
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if not asset:
            return {"error": "application_profile not found", "asset_id": asset_id}
        return {
            "id": str(asset.id),
            "name": asset.name,
            "environment": asset.environment.value,
            "criticality": asset.criticality.value,
            "tags": asset.tags,
            "profile": asset.asset_metadata,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_database_instances(
    token: str,
    environment: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List discovered database instances. Each instance has engine, version, DSN template,
    and baseline row counts. Use when planning a database migration to identify source
    and target instances.
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Asset)
            .where(
                Asset.organization_id == user.organization_id,
                Asset.asset_type == AssetType.database_instance,
            )
            .order_by(Asset.created_at.desc())
            .limit(limit)
        )
        if environment:
            stmt = stmt.where(Asset.environment == environment)

        result = await db.execute(stmt)
        assets = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "environment": a.environment.value,
                "engine": a.asset_metadata.get("engine"),
                "version": a.asset_metadata.get("version"),
                "schema_version": a.asset_metadata.get("schema_version"),
                "created_at": a.created_at.isoformat(),
            }
            for a in assets
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
