# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Tunnel metrics and audit-log REST endpoints.

GET /tunnel/metrics  — real-time agent online/stream stats + 1h dial counts
GET /tunnel/audit    — recent per-dial audit records (security review)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tunnel_audit import TunnelDialAudit
from app.models.user import User, UserRole
from app.routers import require_roles
from app.tunnel.manager import get_manager

router = APIRouter(prefix="/tunnel", tags=["Tunnel"])


@router.get("/metrics")
async def tunnel_metrics(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return tunnel health metrics: online agents, active streams, 1h dial counts."""
    manager = get_manager()
    stats = manager.agent_stats()

    agents_online = sum(1 for v in stats.values() if v.get("online"))
    total_active_streams = sum(v.get("active_streams", 0) for v in stats.values())

    agents_list = [
        {
            "agent_id": agent_id,
            "online": v.get("online", False),
            "active_streams": v.get("active_streams", 0),
        }
        for agent_id, v in stats.items()
    ]

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    recent_dials_result = await db.execute(
        select(func.count()).where(TunnelDialAudit.opened_at >= one_hour_ago)
    )
    recent_dials_1h: int = recent_dials_result.scalar_one() or 0

    denied_dials_result = await db.execute(
        select(func.count()).where(
            TunnelDialAudit.opened_at >= one_hour_ago,
            TunnelDialAudit.close_reason == "denied",
        )
    )
    denied_dials_1h: int = denied_dials_result.scalar_one() or 0

    return {
        "agents_online": agents_online,
        "total_active_streams": total_active_streams,
        "agents": agents_list,
        "recent_dials_1h": recent_dials_1h,
        "denied_dials_1h": denied_dials_1h,
    }


@router.get("/audit")
async def tunnel_audit(
    agent_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return recent dial audit records, optionally filtered by agent_id."""
    q = select(TunnelDialAudit).order_by(TunnelDialAudit.opened_at.desc()).limit(limit)
    if agent_id is not None:
        q = q.where(TunnelDialAudit.agent_id == agent_id)
    result = await db.execute(q)
    rows = result.scalars().all()

    return [
        {
            "id": str(row.id),
            "agent_id": str(row.agent_id),
            "connector_type": row.connector_type,
            "destination_host": row.destination_host,
            "destination_port": row.destination_port,
            "bytes_sent": row.bytes_sent,
            "bytes_recv": row.bytes_recv,
            "opened_at": row.opened_at.isoformat() if row.opened_at else None,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "close_reason": row.close_reason,
        }
        for row in rows
    ]
