# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_asset(asset_id, tags=None):
    from app.models.asset import Asset
    a = Asset()
    a.id = asset_id
    a.tags = tags or []
    return a


def test_resolve_asset_group_by_ids():
    """resolve_asset_group with asset_ids returns filtered assets."""
    from app.services.fleet_executor import resolve_asset_group
    db = MagicMock()
    mock_assets = [_make_asset(1), _make_asset(2)]
    db.execute = MagicMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_assets)))
    ))
    # We test the group resolution logic directly
    group = {"asset_ids": [1, 2]}
    # Patch sqlalchemy query to return mock assets
    with patch("app.services.fleet_executor._query_assets_by_ids", return_value=mock_assets):
        result = resolve_asset_group(group, db)
    assert len(result) == 2


def test_resolve_asset_group_unknown_format_raises():
    from app.services.fleet_executor import resolve_asset_group
    db = MagicMock()
    with pytest.raises(ValueError, match="Unknown asset_group format"):
        resolve_asset_group({"unknown_key": "val"}, db)


@pytest.mark.asyncio
async def test_execute_rolling_restart_empty_assets():
    """Rolling restart with zero assets completes immediately."""
    from app.services.fleet_executor import execute_rolling_restart
    cr = MagicMock()
    cr.parameters = {
        "service_name": "nginx",
        "asset_group": {"asset_ids": []},
        "batch_size_pct": 10,
        "abort_threshold_pct": 25,
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group_async", return_value=[]):
        await execute_rolling_restart(cr, db)

    assert cr.status == "completed"


@pytest.mark.asyncio
async def test_execute_fleet_health_check_empty_assets():
    """Fleet health check with zero assets completes without error."""
    from app.services.fleet_executor import execute_fleet_health_check
    cr = MagicMock()
    cr.parameters = {
        "asset_group": {"asset_ids": []},
        "required_services": [],
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group_async", return_value=[]):
        await execute_fleet_health_check(cr, db)

    assert cr.status == "completed"
    assert "per_host" in cr.step_metadata


@pytest.mark.asyncio
async def test_execute_distribute_file_empty_assets():
    from app.services.fleet_executor import execute_distribute_file
    cr = MagicMock()
    cr.parameters = {
        "file_path": "/etc/test.conf",
        "file_content": "aGVsbG8=",
        "permissions": "0644",
        "asset_group": {"asset_ids": []},
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group_async", return_value=[]):
        await execute_distribute_file(cr, db)

    assert cr.status in ("completed", "completed_with_errors")


@pytest.mark.asyncio
async def test_rolling_restart_aborts_on_threshold():
    """Rolling restart aborts when failure rate exceeds threshold."""
    from app.services.fleet_executor import execute_rolling_restart
    assets = [_make_asset(i) for i in range(4)]

    cr = MagicMock()
    cr.parameters = {
        "service_name": "nginx",
        "asset_group": {"asset_ids": [a.id for a in assets]},
        "batch_size_pct": 25,       # 1 asset per batch (25% of 4)
        "abort_threshold_pct": 25,  # abort if >= 25% fail
    }
    cr.step_metadata = None
    db = AsyncMock()

    # All jobs return running=False (100% failure)
    failed_result = {"running": False, "error": "unit not found"}

    async def fake_dispatch(asset_id, command, params):
        return "job-id"

    async def fake_wait(job_id):
        return failed_result

    with patch("app.services.fleet_executor.resolve_asset_group_async", return_value=assets), \
         patch("app.services.fleet_executor.dispatch_agent_job", side_effect=fake_dispatch), \
         patch("app.services.fleet_executor.wait_for_job_result", side_effect=fake_wait):
        await execute_rolling_restart(cr, db)

    assert cr.status == "batch_aborted"
    assert cr.step_metadata["aborted"] is True
