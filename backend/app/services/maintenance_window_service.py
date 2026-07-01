# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.maintenance_window import MaintenanceWindow


async def is_in_maintenance_window(
    db: AsyncSession,
    asset_tags: list[str],
    enforcement: str = "hard",
) -> Optional[MaintenanceWindow]:
    """Return first active hard-enforcement window matching these tags, or None."""
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.enabled == True,
            MaintenanceWindow.enforcement == enforcement,
        )
    )
    windows = result.scalars().all()
    now = datetime.now(timezone.utc)
    for w in windows:
        window_tags = set(w.applies_to_tags or [])
        if window_tags and not window_tags.intersection(set(asset_tags)):
            continue
        # Check if currently inside the window using croniter
        try:
            from croniter import croniter
            cron = croniter(w.cron_schedule, now)
            prev_start = cron.get_prev(datetime)
            if (now - prev_start).total_seconds() <= w.duration_minutes * 60:
                return w
        except Exception:
            pass
    return None
