# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch


def _make_window(cron="0 2 * * 6", duration=60, tags=None, enabled=True):
    from app.models.maintenance_window import MaintenanceWindow
    w = MaintenanceWindow()
    w.id = 1
    w.cron_schedule = cron
    w.duration_minutes = duration
    w.applies_to_tags = tags
    w.enabled = enabled
    return w


def test_is_window_open_returns_false_when_outside_window():
    from app.routers.maintenance_windows import _check_window_open
    # Use a cron that fires Saturday 02:00; pick a non-Saturday time
    w = _make_window(cron="0 2 * * 6", duration=60)
    # Wednesday 10:00 UTC
    now = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
    is_open, next_open = _check_window_open(w, now)
    assert not is_open
    assert next_open is not None


def test_is_window_open_returns_true_when_inside_window():
    from app.routers.maintenance_windows import _check_window_open
    # Cron fires at 02:00, window is 60 min; pick 02:30 on a Saturday
    w = _make_window(cron="0 2 * * 6", duration=60)
    # Saturday 2026-05-02 02:30 UTC
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    is_open, next_open = _check_window_open(w, now)
    assert is_open
    assert next_open is None


def test_is_window_open_disabled_always_false():
    from app.routers.maintenance_windows import _check_window_open
    w = _make_window(cron="0 2 * * 6", duration=60, enabled=False)
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    is_open, _ = _check_window_open(w, now)
    assert not is_open


def test_is_window_open_for_org_no_windows():
    from app.routers.maintenance_windows import is_window_open_for_org
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([], set(), now)
    assert not result


def test_is_window_open_for_org_global_window_open():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=None)
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"prod", "web"}, now)
    assert result


def test_is_window_open_for_org_tag_window_matching():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=["prod-web"])
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"prod-web", "us-east"}, now)
    assert result


def test_is_window_open_for_org_tag_window_non_matching():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=["prod-web"])
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"staging"}, now)
    assert not result


@pytest.mark.asyncio
async def test_scheduler_promote_queued_changes_imports():
    """Verify the scheduler function is importable and callable as a coroutine."""
    from app.services.scheduler_service import promote_queued_changes
    # Call with a mock db that returns empty results — should not raise
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    await promote_queued_changes(db)
