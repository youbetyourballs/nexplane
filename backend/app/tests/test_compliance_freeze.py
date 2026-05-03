"""
Tests for require_no_active_freeze dependency.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


@pytest.mark.asyncio
async def test_no_freeze_window_passes():
    from app.compliance.freeze import require_no_active_freeze
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    user = MagicMock(roles=[])

    # Should not raise
    await require_no_active_freeze(justification=None, db=db, current_user=user)


@pytest.mark.asyncio
async def test_active_freeze_blocks_normal_user():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    user = MagicMock(roles=["viewer"])

    with pytest.raises(HTTPException) as exc_info:
        await require_no_active_freeze(justification=None, db=db, current_user=user)
    assert exc_info.value.status_code == 423
    assert "change_freeze_active" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_active_freeze_bypass_without_justification_raises_422():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    user = MagicMock(roles=["emergency_bypass"])

    with pytest.raises(HTTPException) as exc_info:
        await require_no_active_freeze(justification=None, db=db, current_user=user)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_active_freeze_bypass_with_justification_passes():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    db.add = MagicMock()
    db.flush = AsyncMock()
    user = MagicMock(roles=["emergency_bypass"])

    with patch("app.compliance.freeze.log_freeze_bypass", new=AsyncMock()):
        # Should not raise
        await require_no_active_freeze(
            justification="Critical production incident P1-2026-0503",
            db=db,
            current_user=user,
        )
