# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for find_similar_assets and get_migration_precedents."""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp_tools.planning_context  # noqa: F401


def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    return u


def _make_asset(org_id, asset_type="server", environment="prod", tags=None):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = "test-host"
    a.asset_type = MagicMock()
    a.asset_type.value = asset_type
    a.environment = MagicMock()
    a.environment.value = environment
    a.tags = tags or []
    a.asset_metadata = {}
    return a


def _make_cr(org_id, status="completed", days_ago=3):
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.organization_id = org_id
    cr.change_type = MagicMock()
    cr.change_type.value = "patch_packages"
    cr.status = MagicMock()
    cr.status.value = status
    cr.target_asset_ids = [str(uuid.uuid4())]
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    cr.created_at = ts
    cr.updated_at = ts + timedelta(minutes=10)
    cr.stateful_approved_at = ts
    return cr


@pytest.mark.asyncio
async def test_find_similar_assets_jaccard_score():
    """find_similar_assets scores assets by tag Jaccard similarity."""
    from app.mcp_tools.planning_context import find_similar_assets

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    target = _make_asset(org_id, tags=["env=prod", "role=web", "team=platform"])
    # High similarity: 2/4 shared
    candidate1 = _make_asset(org_id, tags=["env=prod", "role=web", "team=security"])
    # Low similarity: 1/5 shared
    candidate2 = _make_asset(org_id, tags=["env=prod", "role=db", "team=data"])

    mock_db = AsyncMock()
    target_result = MagicMock()
    target_result.scalar_one_or_none.return_value = target
    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [candidate1, candidate2]
    mock_db.execute = AsyncMock(side_effect=[target_result, candidates_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await find_similar_assets("token", str(target.id))

    assert len(result) == 2
    # candidate1 must rank higher
    assert result[0]["similarity_score"] >= result[1]["similarity_score"]
    assert "env=prod" in result[0]["shared_tags"]


@pytest.mark.asyncio
async def test_find_similar_assets_not_found():
    """find_similar_assets returns error when asset not found."""
    from app.mcp_tools.planning_context import find_similar_assets

    user = _make_user()
    mock_db = AsyncMock()
    not_found = MagicMock()
    not_found.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=not_found)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await find_similar_assets("token", str(uuid.uuid4()))

    assert result[0]["error"] == "Asset not found"


@pytest.mark.asyncio
async def test_get_migration_precedents_success_rate():
    """get_migration_precedents calculates correct success_rate."""
    from app.mcp_tools.planning_context import get_migration_precedents

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    cr1 = _make_cr(org_id, status="completed")
    cr2 = _make_cr(org_id, status="completed")
    cr3 = _make_cr(org_id, status="failed")
    cr4 = _make_cr(org_id, status="rolled_back")

    mock_db = AsyncMock()
    crs_result = MagicMock()
    crs_result.scalars.return_value.all.return_value = [cr1, cr2, cr3, cr4]
    # runs for failed/rolled_back: return None
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[crs_result, run_result, run_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_migration_precedents("token", "patch_packages")

    assert result["total_executions"] == 4
    assert result["success_rate"] == 0.5
    assert result["rollback_frequency"] == 0.25


@pytest.mark.asyncio
async def test_get_migration_precedents_no_history():
    """get_migration_precedents returns None rates when no history."""
    from app.mcp_tools.planning_context import get_migration_precedents

    user = _make_user()
    mock_db = AsyncMock()
    crs_result = MagicMock()
    crs_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=crs_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_migration_precedents("token", "patch_packages")

    assert result["total_executions"] == 0
    assert result["success_rate"] is None
