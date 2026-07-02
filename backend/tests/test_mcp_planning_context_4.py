# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for get_environment_diff and get_project_precedents."""
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


def _make_asset(org_id, name="host"):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = name
    return a


@pytest.mark.asyncio
async def test_get_environment_diff_requires_two_assets():
    """get_environment_diff returns error if fewer than 2 asset_ids provided."""
    from app.mcp_tools.planning_context import get_environment_diff

    user = _make_user()
    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context.AsyncSessionLocal", return_value=mock_db_cm):
        result = await get_environment_diff("token", [str(uuid.uuid4())])

    assert "error" in result


@pytest.mark.asyncio
async def test_get_environment_diff_missing_cache():
    """get_environment_diff populates missing_data when cache table has no rows."""
    from app.mcp_tools.planning_context import get_environment_diff

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    a1 = _make_asset(org_id, "host-1")
    a2 = _make_asset(org_id, "host-2")

    mock_db = AsyncMock()
    # assets fetch
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [a1, a2]
    # cache queries — fetchone returns None for each (8 calls: 2 assets x 4 cache_types)
    none_result = MagicMock()
    none_result.fetchone.return_value = None
    mock_db.execute = AsyncMock(side_effect=[assets_result] + [none_result] * 8)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_environment_diff("token", [str(a1.id), str(a2.id)])

    assert len(result["missing_data"]) == 2
    assert result["differences"] == []


@pytest.mark.asyncio
async def test_get_environment_diff_detects_drift():
    """get_environment_diff identifies packages present on one host but not the other."""
    from app.mcp_tools.planning_context import get_environment_diff

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    a1 = _make_asset(org_id, "host-1")
    a2 = _make_asset(org_id, "host-2")

    packages_a1 = [{"name": "openssl", "version": "1.1.1"}, {"name": "curl", "version": "7.80"}]
    packages_a2 = [{"name": "openssl", "version": "1.1.1"}, {"name": "wget", "version": "1.21"}]

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [a1, a2]

    def _make_cache_row(data):
        r = MagicMock()
        r.fetchone.return_value = (data,)
        return r

    def _make_none():
        r = MagicMock()
        r.fetchone.return_value = None
        return r

    # Sequence: assets fetch, then for a1: packages, users(none), services(none), ports(none)
    # then for a2: packages, users(none), services(none), ports(none)
    mock_db.execute = AsyncMock(side_effect=[
        assets_result,
        _make_cache_row(packages_a1), _make_none(), _make_none(), _make_none(),
        _make_cache_row(packages_a2), _make_none(), _make_none(), _make_none(),
    ])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_environment_diff("token", [str(a1.id), str(a2.id)])

    # openssl is common; curl is only on a1; wget is only on a2
    assert any("openssl" in p for p in result["common"]["packages"])
    diff_values = [d["value"] for d in result["differences"] if d["field"] == "packages"]
    assert any("curl" in v for v in diff_values)
    assert any("wget" in v for v in diff_values)


@pytest.mark.asyncio
async def test_get_project_precedents_returns_empty_for_no_match():
    """get_project_precedents returns empty list when pg_trgm finds no matches."""
    from app.mcp_tools.planning_context import get_project_precedents

    user = _make_user()
    mock_db = AsyncMock()
    trgm_result = MagicMock()
    trgm_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=trgm_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_project_precedents("token", "rotate all SSH keys in prod")

    assert result == []


@pytest.mark.asyncio
async def test_get_project_precedents_structure():
    """get_project_precedents returns correctly structured precedent records."""
    from app.mcp_tools.planning_context import get_project_precedents

    org_id = uuid.uuid4()
    user = _make_user(org_id)

    proj_id = uuid.uuid4()
    cr_id = uuid.uuid4()

    class Row:
        def __init__(self):
            self._d = [proj_id, "Key Rotation Q1", "rotate SSH keys production", 0.72]

        def __getitem__(self, i):
            return self._d[i]

    mock_pcr = MagicMock()
    mock_pcr.sequence_order = 0
    mock_pcr.change_request_id = cr_id

    mock_cr = MagicMock()
    mock_cr.id = cr_id
    mock_cr.status = MagicMock()
    mock_cr.status.value = "completed"
    mock_cr.change_type = MagicMock()
    mock_cr.change_type.value = "rotate_ssh_keys"
    mock_cr.title = "Rotate SSH keys"
    mock_cr.updated_at = datetime.now(timezone.utc)

    mock_proj = MagicMock()
    mock_proj.id = proj_id
    mock_proj.created_at = datetime.now(timezone.utc) - timedelta(days=7)

    mock_db = AsyncMock()
    trgm_result = MagicMock()
    trgm_result.fetchall.return_value = [Row()]
    pcrs_result = MagicMock()
    pcrs_result.scalars.return_value.all.return_value = [mock_pcr]
    cr_result = MagicMock()
    cr_result.scalar_one_or_none.return_value = mock_cr
    proj_result = MagicMock()
    proj_result.scalar_one_or_none.return_value = mock_proj

    mock_db.execute = AsyncMock(side_effect=[trgm_result, pcrs_result, cr_result, proj_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_project_precedents("token", "rotate SSH keys")

    assert len(result) == 1
    r = result[0]
    assert r["similarity_score"] == pytest.approx(0.72, abs=0.01)
    assert r["completed_crs"] == 1
    assert r["total_crs"] == 1
    assert len(r["cr_sequence_summary"]) == 1
    assert r["cr_sequence_summary"][0]["change_type"] == "rotate_ssh_keys"
