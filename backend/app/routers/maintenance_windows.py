from datetime import datetime, timezone, timedelta
from typing import Optional

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.maintenance_window import MaintenanceWindow
from app.models.user import User
from app.routers import current_user
from app.schemas.maintenance_window import (
    MaintenanceWindowCreate,
    MaintenanceWindowRead,
    MaintenanceWindowStatusRead,
)

router = APIRouter(prefix="/maintenance-windows", tags=["Maintenance Windows"])


async def _get_window(db: AsyncSession, window_id: int, org_id) -> MaintenanceWindow:
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.id == window_id,
            MaintenanceWindow.organization_id == org_id,
        )
    )
    window = result.scalar_one_or_none()
    if not window:
        raise HTTPException(status_code=404, detail="Maintenance window not found")
    return window


@router.get("", response_model=list[MaintenanceWindowRead])
async def list_maintenance_windows(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.organization_id == user.organization_id
        ).order_by(MaintenanceWindow.id)
    )
    return result.scalars().all()


@router.post("", response_model=MaintenanceWindowRead, status_code=201)
async def create_maintenance_window(
    body: MaintenanceWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = MaintenanceWindow(
        organization_id=user.organization_id,
        name=body.name,
        cron_schedule=body.cron_schedule,
        duration_minutes=body.duration_minutes,
        applies_to_tags=body.applies_to_tags,
        enabled=body.enabled,
    )
    db.add(window)
    await db.commit()
    await db.refresh(window)
    return window


@router.get("/{window_id}", response_model=MaintenanceWindowRead)
async def get_maintenance_window(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_window(db, window_id, user.organization_id)


@router.put("/{window_id}", response_model=MaintenanceWindowRead)
async def update_maintenance_window(
    window_id: int,
    body: MaintenanceWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    window.name = body.name
    window.cron_schedule = body.cron_schedule
    window.duration_minutes = body.duration_minutes
    window.applies_to_tags = body.applies_to_tags
    window.enabled = body.enabled
    await db.commit()
    await db.refresh(window)
    return window


@router.delete("/{window_id}", status_code=204)
async def delete_maintenance_window(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    await db.delete(window)
    await db.commit()


@router.get("/{window_id}/status", response_model=MaintenanceWindowStatusRead)
async def get_maintenance_window_status(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    now = datetime.now(timezone.utc)
    is_open, next_open = _check_window_open(window, now)
    return MaintenanceWindowStatusRead(
        id=window.id,
        is_open=is_open,
        next_open_at=next_open.isoformat() if next_open else None,
    )


def _check_window_open(window: MaintenanceWindow, now: datetime) -> tuple[bool, Optional[datetime]]:
    """Returns (is_open, next_open_at). next_open_at is None if currently open."""
    if not window.enabled:
        return False, None
    try:
        cron = croniter(window.cron_schedule, now)
        last_open = cron.get_prev(datetime)
        last_open = last_open.replace(tzinfo=timezone.utc)
        window_close = last_open + timedelta(minutes=window.duration_minutes)
        if last_open <= now <= window_close:
            return True, None
        # Find next open
        cron_next = croniter(window.cron_schedule, now)
        next_open = cron_next.get_next(datetime).replace(tzinfo=timezone.utc)
        return False, next_open
    except Exception:
        return False, None


def is_window_open_for_org(windows: list[MaintenanceWindow], asset_tags: set[str], now: datetime) -> bool:
    """Helper used by the scheduler and approve endpoint."""
    for w in windows:
        if not w.enabled:
            continue
        is_open, _ = _check_window_open(w, now)
        if not is_open:
            continue
        if w.applies_to_tags is None:
            return True  # applies to all
        if asset_tags & set(w.applies_to_tags):
            return True
    return False
