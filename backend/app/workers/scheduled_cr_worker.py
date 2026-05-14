from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy import select, and_
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus

logger = logging.getLogger(__name__)


async def execute_scheduled_crs() -> None:
    """Execute CRs whose execute_at time has passed."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ChangeRequest).where(
                and_(
                    ChangeRequest.execute_at <= now,
                    ChangeRequest.execute_at.isnot(None),
                    ChangeRequest.status == ChangeRequestStatus.approved,
                )
            )
        )
        for cr in result.scalars():
            try:
                from app.workflows.execute_change_workflow import trigger_change_workflow
                await trigger_change_workflow(str(cr.id))
                logger.info(f"Triggered scheduled CR {cr.id}")
            except Exception as e:
                logger.warning(f"Failed to trigger scheduled CR {cr.id}: {e}")
