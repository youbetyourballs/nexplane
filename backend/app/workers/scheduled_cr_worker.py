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
                from app.services.change_execution_service import ChangeExecutionService
                from app.database import AsyncSessionLocal as _ASL
                async with _ASL() as exec_db:
                    await ChangeExecutionService.start(cr.id, cr.requester_id, "scheduled", exec_db)
                logger.info(f"Triggered scheduled CR {cr.id}")
            except Exception as e:
                logger.warning(f"Failed to trigger scheduled CR {cr.id}: {e}")
