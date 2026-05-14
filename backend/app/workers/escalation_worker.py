from __future__ import annotations
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services.notification_service import NotificationService, NotificationEvent
import logging

logger = logging.getLogger(__name__)


async def check_emergency_escalations() -> None:
    """Escalate emergency CRs that have been awaiting approval too long."""
    async with AsyncSessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = await db.execute(
            select(ChangeRequest).where(
                and_(
                    ChangeRequest.priority == "emergency",
                    ChangeRequest.status == ChangeRequestStatus.awaiting_approval,
                    ChangeRequest.updated_at < cutoff,
                )
            )
        )
        for cr in result.scalars():
            try:
                from app.models.user import User, UserRole
                admins = (await db.execute(
                    select(User).where(
                        User.organization_id == cr.organization_id,
                        User.role == UserRole.admin,
                    )
                )).scalars().all()
                if admins:
                    svc = NotificationService(db)
                    await svc.emit(NotificationEvent(
                        event_type="cr.escalated",
                        organization_id=str(cr.organization_id),
                        resource_id=str(cr.id),
                        resource_type="change_request",
                        message=f"EMERGENCY CR '{cr.title}' has not been approved — escalating to admins",
                        recipients=[str(u.id) for u in admins],
                    ))
            except Exception as e:
                logger.warning(f"Escalation failed for CR {cr.id}: {e}")
