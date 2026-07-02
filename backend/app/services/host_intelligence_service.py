# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Cache-aside service for host intelligence MCP tools.

Flow: check cache → dispatch agent job on miss → persist result → return data.
TTL is 300 s by default. Cache is per (org_id, asset_id, tool_name).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_intelligence_cache import McpIntelligenceCache


async def run_intelligence_tool(
    db: AsyncSession,
    user,
    asset_id: str,
    tool_name: str,
    command: str,
    params: dict,
    timeout: int = 120,
) -> dict[str, Any]:
    """
    Return cached result if fresh, otherwise dispatch agent job, persist, and return.
    """
    org_id = user.organization_id
    asset_uuid = uuid.UUID(asset_id)

    cached = await _get_cached(db, org_id, asset_uuid, tool_name)
    if cached is not None:
        return cached

    result = await _dispatch(command, params, asset_id, timeout)

    await _store(db, org_id, asset_uuid, tool_name, result)
    return result


async def invalidate_cache(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: str,
) -> None:
    """Delete all cache entries for an asset."""
    asset_uuid = uuid.UUID(asset_id)
    await db.execute(
        delete(McpIntelligenceCache).where(
            McpIntelligenceCache.org_id == org_id,
            McpIntelligenceCache.asset_id == asset_uuid,
        )
    )
    await db.commit()


async def _get_cached(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    tool_name: str,
) -> dict[str, Any] | None:
    """Return result dict if a live cache entry exists, else None."""
    result = await db.execute(
        select(McpIntelligenceCache).where(
            McpIntelligenceCache.org_id == org_id,
            McpIntelligenceCache.asset_id == asset_id,
            McpIntelligenceCache.tool_name == tool_name,
            McpIntelligenceCache.cached_at
            > text("now() - interval '300 seconds'"),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row.result


async def _dispatch(
    command: str,
    params: dict,
    asset_id: str,
    timeout: int,
) -> dict[str, Any]:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    return await dispatch_agent_job(
        command=command,
        parameters=params,
        asset_ids=[asset_id],
        timeout_seconds=timeout,
    )


async def _store(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    tool_name: str,
    result: dict[str, Any],
) -> None:
    """Upsert: delete stale entry then insert fresh one."""
    await db.execute(
        delete(McpIntelligenceCache).where(
            McpIntelligenceCache.org_id == org_id,
            McpIntelligenceCache.asset_id == asset_id,
            McpIntelligenceCache.tool_name == tool_name,
        )
    )
    entry = McpIntelligenceCache(
        id=uuid.uuid4(),
        org_id=org_id,
        asset_id=asset_id,
        tool_name=tool_name,
        result=result,
        cached_at=datetime.now(timezone.utc),
        ttl_seconds=300,
    )
    db.add(entry)
    await db.commit()
