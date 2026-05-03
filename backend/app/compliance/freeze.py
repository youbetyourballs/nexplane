import logging
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.compliance import ChangeFreezeWindow
from app.models.user import User
from app.routers import current_user

logger = logging.getLogger(__name__)


async def require_no_active_freeze(
    justification: str | None = Header(None, alias="X-Emergency-Justification"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(current_user),
) -> None:
    """
    FastAPI dependency injected into change-request approve and execute routes.
    Raises HTTP 423 if a freeze window is active and the caller does not have
    the emergency_bypass role. Requires X-Emergency-Justification header when
    bypassing. Logs bypass events to the audit log.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChangeFreezeWindow).where(
            ChangeFreezeWindow.start_at <= now,
            ChangeFreezeWindow.end_at >= now,
        ).limit(1)
    )
    active = result.scalar_one_or_none()
    if active is None:
        return

    user_roles = getattr(current_user, "roles", []) or []
    if active.emergency_bypass_role not in user_roles:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "change_freeze_active",
                "message": f"Change freeze active until {active.end_at.isoformat()}",
                "reason": active.reason,
                "freeze_id": str(active.id),
            },
        )

    # User has bypass role — require justification header
    if not justification:
        raise HTTPException(
            status_code=422,
            detail="X-Emergency-Justification header required to bypass an active change freeze",
        )

    await log_freeze_bypass(db, current_user, active, justification)


async def log_freeze_bypass(
    db: AsyncSession,
    user: User,
    freeze: ChangeFreezeWindow,
    justification: str,
) -> None:
    """Record a freeze bypass event in the audit log."""
    try:
        from app.services.audit_service import record_event
        await record_event(
            db,
            user.organization_id,
            "change_freeze.bypassed",
            {
                "freeze_id": str(freeze.id),
                "reason": freeze.reason,
                "justification": justification,
            },
            actor_id=user.id,
        )
    except Exception as exc:
        logger.warning("Failed to log freeze bypass: %s", exc)
