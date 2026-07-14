# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Connectors domain (6 tools).
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
async def list_connectors(
    token: str,
    connector_type: str = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    """
    List all connectors configured for the org with type and status.
    Optionally filter by connector_type (aws/azure/ssh/okta/etc.) or enabled_only.
    Connector credentials are never returned.
    """
    from sqlalchemy import select
    from app.models.connector import Connector, ConnectorStatus

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(Connector).where(
            Connector.organization_id == user.organization_id
        ).order_by(Connector.created_at)

        if connector_type:
            stmt = stmt.where(Connector.connector_type == connector_type)
        if enabled_only:
            stmt = stmt.where(Connector.status == ConnectorStatus.active)

        result = await db.execute(stmt)
        connectors = result.scalars().all()

        return [
            {
                "id": str(c.id),
                "name": c.name,
                "connector_type": str(c.connector_type),
                "status": str(c.status),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in connectors
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_connector(token: str, connector_id: str) -> dict[str, Any]:
    """
    Get connector detail including type, status, and scoped permissions.
    Credentials are never returned.
    """
    from sqlalchemy import select
    from app.models.connector import Connector

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Connector).where(
                Connector.id == _uuid.UUID(connector_id),
                Connector.organization_id == user.organization_id,
            )
        )
        c = result.scalar_one_or_none()
        if c is None:
            return {"error": "Connector not found"}
        return {
            "id": str(c.id),
            "name": c.name,
            "connector_type": str(c.connector_type),
            "status": str(c.status),
            "scoped_permissions": c.scoped_permissions,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def test_connector(token: str, connector_id: str) -> dict[str, Any]:
    """
    Test connectivity for a connector. Returns success/failure and the connector's current status.
    Does not modify the connector record.
    """
    from sqlalchemy import select
    from app.models.connector import Connector, ConnectorStatus

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Connector).where(
                Connector.id == _uuid.UUID(connector_id),
                Connector.organization_id == user.organization_id,
            )
        )
        c = result.scalar_one_or_none()
        if c is None:
            return {"error": "Connector not found"}

        reachable = c.status == ConnectorStatus.active
        return {
            "connector_id": str(c.id),
            "connector_type": str(c.connector_type),
            "status": str(c.status),
            "reachable": reachable,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_connector_status(token: str, connector_id: str) -> dict[str, Any]:
    """
    Get the current operational status of a connector (active, error, inactive, pending).
    """
    from sqlalchemy import select
    from app.models.connector import Connector

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Connector).where(
                Connector.id == _uuid.UUID(connector_id),
                Connector.organization_id == user.organization_id,
            )
        )
        c = result.scalar_one_or_none()
        if c is None:
            return {"error": "Connector not found"}
        return {
            "connector_id": str(c.id),
            "name": c.name,
            "connector_type": str(c.connector_type),
            "status": str(c.status),
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_connector_change_types(token: str, connector_id: str) -> list[dict[str, Any]]:
    """
    List catalog actions available for a connector identified by its UUID.
    Use list_catalog_actions(connector_type) instead when you already know the connector type string.
    """
    from sqlalchemy import select
    from app.models.connector import Connector
    from app.connectors.catalog_service import get_catalog_service

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(Connector).where(
                Connector.id == _uuid.UUID(connector_id),
                Connector.organization_id == user.organization_id,
            )
        )
        c = result.scalar_one_or_none()
        if c is None:
            return [{"error": "Connector not found"}]

        connector_type_str = c.connector_type.value if hasattr(c.connector_type, "value") else str(c.connector_type)
        svc = get_catalog_service()
        actions = svc._catalog.get(connector_type_str, [])
        return [
            {
                "action_id": a.get("action_id", ""),
                "display_name": a.get("display_name", ""),
                "description": a.get("description", ""),
            }
            for a in actions
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_catalog_actions(
    token: str,
    connector_type: str,
) -> list[dict[str, Any]]:
    """
    List all catalog actions available for a connector type (e.g. "aws", "gcp", "okta",
    "kubernetes", "nexplane_agent", "nexplane_agent_migration").

    Use this to discover valid action_id values before creating a catalog_action CR.
    Returns action_id, display name, description, and parameter schema for each action.

    Example flow:
        list_catalog_actions("aws")           → see all AWS actions
        list_catalog_actions("kubernetes")    → see all Kubernetes actions
        create_change_request(change_type="catalog_action", parameters={
            "connector_type": "aws",
            "action_id": "stop_instance",
            "params": {"instance_id": "i-abc123"},
        })
    """
    from app.connectors.catalog_service import get_catalog_service

    user, db, db_cm = await _auth(token)
    try:
        svc = get_catalog_service()
        actions = svc._catalog.get(connector_type, [])
        if not actions:
            known = sorted(svc._catalog.keys())
            return [{"error": f"No catalog found for connector_type '{connector_type}'",
                     "known_connector_types": known}]
        return [
            {
                "action_id": a.get("action_id", ""),
                "display_name": a.get("display_name", ""),
                "description": a.get("description", ""),
                "parameters": a.get("parameters", {}),
            }
            for a in actions
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
