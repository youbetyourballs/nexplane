# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.project_phase import ProjectPhase

logger = logging.getLogger(__name__)


async def check_soak_timers() -> None:
    """Advance project phases that have completed their soak period."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ProjectPhase).where(
                ProjectPhase.status == "soaking",
                ProjectPhase.soak_started_at.isnot(None),
            )
        )
        for phase in result.scalars():
            elapsed_hours = (
                datetime.now(timezone.utc) - phase.soak_started_at
            ).total_seconds() / 3600
            if elapsed_hours >= phase.soak_hours:
                phase.status = "completed"
                phase.soak_completed_at = datetime.now(timezone.utc)
                logger.info(f"ProjectPhase {phase.id} soak complete after {elapsed_hours:.1f}h")
        await db.commit()
