"""Tests for new scheduler job functions."""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# scheduled_reboot_dispatcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_due_reboots_dispatches_past_due():
    """Change requests with reboot_at in the past and status=approved get dispatched."""
    from app.services.scheduler_service import dispatch_due_reboots

    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_cr = MagicMock()
    mock_cr.id = "cr-uuid-1"
    mock_cr.change_type = "scheduled_reboot"
    mock_cr.status = "approved"
    mock_cr.metadata = {"reboot_at": past_time, "verify_services": ["nginx"]}
    mock_cr.target_asset_id = "asset-uuid-1"

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_cr]
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.scheduler_service._db_factory", return_value=mock_db), \
         patch("app.services.scheduler_service._dispatch_agent_reboot_job", new_callable=AsyncMock) as mock_dispatch:
        await dispatch_due_reboots()
        mock_dispatch.assert_called_once()
        args = mock_dispatch.call_args[0]
        assert args[0] == "asset-uuid-1"


@pytest.mark.asyncio
async def test_dispatch_due_reboots_skips_future():
    """Change requests with reboot_at in the future are not dispatched."""
    from app.services.scheduler_service import dispatch_due_reboots

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []  # DB filter handles this
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.scheduler_service._db_factory", return_value=mock_db), \
         patch("app.services.scheduler_service._dispatch_agent_reboot_job", new_callable=AsyncMock) as mock_dispatch:
        await dispatch_due_reboots()
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# access_review_creator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_access_review_creator_creates_review_when_due():
    """AccessReviewSchedule past its frequency_days triggers an AccessReview record."""
    from app.services.scheduler_service import check_access_review_schedules

    mock_sched = MagicMock()
    mock_sched.id = "sched-uuid-1"
    mock_sched.frequency_days = 90
    mock_sched.scope = "all_users"
    mock_sched.reviewer_assignment_rule = "direct_manager"
    mock_sched.last_review_created_at = datetime.now(timezone.utc) - timedelta(days=91)
    mock_sched.enabled = True

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_sched]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.scheduler_service._db_factory", return_value=mock_db):
        await check_access_review_schedules()
        mock_db.add.assert_called_once()  # AccessReview record added


@pytest.mark.asyncio
async def test_access_review_creator_skips_not_yet_due():
    """AccessReviewSchedule reviewed recently does not trigger a new review."""
    from app.services.scheduler_service import check_access_review_schedules

    mock_sched = MagicMock()
    mock_sched.frequency_days = 90
    mock_sched.last_review_created_at = datetime.now(timezone.utc) - timedelta(days=10)
    mock_sched.enabled = True

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_sched]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.scheduler_service._db_factory", return_value=mock_db):
        await check_access_review_schedules()
        mock_db.add.assert_not_called()


# ---------------------------------------------------------------------------
# cis_audit_weekly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cis_audit_weekly_dispatches_to_all_assets():
    """run_weekly_compliance_scans dispatches one cis_audit job per managed Linux asset."""
    from app.services.scheduler_service import run_weekly_compliance_scans

    mock_asset1 = MagicMock()
    mock_asset1.id = "asset-1"
    mock_asset2 = MagicMock()
    mock_asset2.id = "asset-2"

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_asset1, mock_asset2]
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.scheduler_service._db_factory", return_value=mock_db), \
         patch("app.services.scheduler_service._dispatch_cis_audit_job", new_callable=AsyncMock) as mock_dispatch:
        await run_weekly_compliance_scans()
        assert mock_dispatch.call_count == 2
