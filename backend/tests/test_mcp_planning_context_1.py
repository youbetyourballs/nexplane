# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Unit tests for get_asset_history and get_fleet_context MCP tools.
Uses mock DB pattern matching the rest of the test suite.
"""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp_tools.planning_context  # noqa: F401 — ensure tools are registered


def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    u.email = "admin@test.com"
    return u


def _make_cr(org_id, asset_id, status="completed", change_type="patch_packages", days_ago=5):
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.organization_id = org_id
    cr.target_asset_ids = [str(asset_id)]
    cr.status = MagicMock()
    cr.status.value = status
    cr.change_type = MagicMock()
    cr.change_type.value = change_type
    cr.title = f"Test CR {status}"
    cr.description = "test notes"
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    cr.created_at = ts
    cr.updated_at = ts + timedelta(minutes=5)
    cr.stateful_approved_at = ts
    cr.applied_at = ts + timedelta(minutes=3)
    return cr


def _make_asset(org_id, asset_type="server", environment="prod", tags=None, meta=None):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = "test-host"
    a.asset_type = MagicMock()
    a.asset_type.value = asset_type
    a.environment = MagicMock()
    a.environment.value = environment
    a.criticality = MagicMock()
    a.criticality.value = "high"
    a.tags = tags or []
    a.asset_metadata = meta or {}
    a.connector_id = None
    a.created_at = datetime.now(timezone.utc)
    return a


@pytest.mark.asyncio
async def test_get_asset_history_returns_matching_crs():
    """get_asset_history returns CRs targeting the given asset."""
    from app.mcp_tools.planning_context import get_asset_history

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    cr = _make_cr(org_id, asset_id, status="completed")
    other_cr = _make_cr(org_id, uuid.uuid4(), status="completed")  # different asset

    mock_db = AsyncMock()
    # First execute: all CRs
    crs_result = MagicMock()
    crs_result.scalars.return_value.all.return_value = [cr, other_cr]
    # Subsequent executes: approval lookups (return None)
    approval_result = MagicMock()
    approval_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[crs_result, approval_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context.AsyncSessionLocal", return_value=mock_db_cm):
        with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
            result = await get_asset_history("token", str(asset_id))

    assert len(result) == 1
    assert result[0]["outcome"] == "success"
    assert result[0]["rolled_back"] is False
    assert result[0]["change_type"] == "patch_packages"


@pytest.mark.asyncio
async def test_get_asset_history_rolled_back_outcome():
    """get_asset_history returns rolled_back outcome for rolled_back status."""
    from app.mcp_tools.planning_context import get_asset_history

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    cr = _make_cr(org_id, asset_id, status="rolled_back")

    mock_db = AsyncMock()
    crs_result = MagicMock()
    crs_result.scalars.return_value.all.return_value = [cr]
    approval_result = MagicMock()
    approval_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[crs_result, approval_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_asset_history("token", str(asset_id))

    assert result[0]["rolled_back"] is True
    assert result[0]["outcome"] == "rolled_back"


@pytest.mark.asyncio
async def test_get_asset_history_duration_seconds():
    """get_asset_history computes duration_seconds from approved_at to updated_at."""
    from app.mcp_tools.planning_context import get_asset_history

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    cr = _make_cr(org_id, asset_id)
    # approved_at is cr.stateful_approved_at, updated_at is 5 minutes later
    now = datetime.now(timezone.utc)
    cr.stateful_approved_at = now
    cr.updated_at = now + timedelta(seconds=300)

    mock_db = AsyncMock()
    crs_result = MagicMock()
    crs_result.scalars.return_value.all.return_value = [cr]
    approval_result = MagicMock()
    approval_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[crs_result, approval_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_asset_history("token", str(asset_id))

    assert result[0]["duration_seconds"] == pytest.approx(300.0, abs=1.0)


@pytest.mark.asyncio
async def test_get_fleet_context_no_filters_returns_all():
    """get_fleet_context with no filters returns all org assets."""
    from app.mcp_tools.planning_context import get_fleet_context

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    asset1 = _make_asset(org_id)
    asset2 = _make_asset(org_id, environment="dev")

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [asset1, asset2]
    # findings count calls (one per asset) + CR lookup calls (one per asset)
    findings_count1 = MagicMock()
    findings_count1.scalar.return_value = 0
    findings_count2 = MagicMock()
    findings_count2.scalar.return_value = 2
    cr_result1 = MagicMock()
    cr_result1.scalars.return_value.all.return_value = []
    cr_result2 = MagicMock()
    cr_result2.scalars.return_value.all.return_value = []
    # sequence: assets, then per-asset: findings, cr_result (full)
    mock_db.execute = AsyncMock(side_effect=[
        assets_result,
        findings_count1, cr_result1,
        findings_count2, cr_result2,
    ])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_fleet_context("token")

    assert len(result) == 2
    assert result[1]["open_findings_count"] == 2


@pytest.mark.asyncio
async def test_get_fleet_context_os_filter():
    """get_fleet_context os filter excludes non-matching assets."""
    from app.mcp_tools.planning_context import get_fleet_context

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    linux_asset = _make_asset(org_id, meta={"os": "ubuntu 22.04"})
    windows_asset = _make_asset(org_id, meta={"os": "Windows Server 2022"})

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [linux_asset, windows_asset]
    findings_count = MagicMock()
    findings_count.scalar.return_value = 0
    cr_full = MagicMock()
    cr_full.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[
        assets_result,
        findings_count, cr_full,
    ])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_fleet_context("token", os="ubuntu")

    assert len(result) == 1
    assert result[0]["metadata"]["os"] == "ubuntu 22.04"


@pytest.mark.asyncio
async def test_get_fleet_context_has_open_findings_false():
    """has_open_findings=False excludes assets that have findings."""
    from app.mcp_tools.planning_context import get_fleet_context

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    clean_asset = _make_asset(org_id)
    dirty_asset = _make_asset(org_id)

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [clean_asset, dirty_asset]
    findings_clean = MagicMock()
    findings_clean.scalar.return_value = 0
    cr_clean = MagicMock()
    cr_clean.scalars.return_value.all.return_value = []
    findings_dirty = MagicMock()
    findings_dirty.scalar.return_value = 3
    mock_db.execute = AsyncMock(side_effect=[
        assets_result,
        findings_clean, cr_clean,
        findings_dirty,
    ])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_fleet_context("token", has_open_findings=False)

    assert len(result) == 1
    assert result[0]["open_findings_count"] == 0
