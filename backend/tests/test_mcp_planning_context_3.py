# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for get_cross_host_dependency_map and get_kernel_eol_status."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp_tools.planning_context  # noqa: F401


def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    return u


def _make_dep(org_id, from_id, to_id, dep_type="connects_to"):
    d = MagicMock()
    d.id = uuid.uuid4()
    d.organization_id = org_id
    d.dependent_asset_id = from_id
    d.dependency_asset_id = to_id
    d.dependency_type = dep_type
    return d


def _make_asset(org_id, name="host", asset_type="server", meta=None):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = name
    a.asset_type = MagicMock()
    a.asset_type.value = asset_type
    a.environment = MagicMock()
    a.environment.value = "prod"
    a.tags = []
    a.asset_metadata = meta or {}
    a.connector_id = None
    return a


@pytest.mark.asyncio
async def test_dependency_map_internal_edge():
    """get_cross_host_dependency_map marks edges within the set as internal=True."""
    from app.mcp_tools.planning_context import get_cross_host_dependency_map

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    dep = _make_dep(org_id, id1, id2)

    mock_db = AsyncMock()
    deps_result = MagicMock()
    deps_result.scalars.return_value.all.return_value = [dep]
    mock_db.execute = AsyncMock(return_value=deps_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_cross_host_dependency_map("token", [str(id1), str(id2)])

    assert len(result["edges"]) == 1
    assert result["edges"][0]["internal"] is True
    assert result["external_dependencies"] == []


@pytest.mark.asyncio
async def test_dependency_map_external_edge():
    """get_cross_host_dependency_map marks edges to outside assets as internal=False."""
    from app.mcp_tools.planning_context import get_cross_host_dependency_map

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    id1 = uuid.uuid4()
    external_id = uuid.uuid4()
    dep = _make_dep(org_id, id1, external_id)

    external_asset = _make_asset(org_id, name="external-db")
    external_asset.id = external_id

    mock_db = AsyncMock()
    deps_result = MagicMock()
    deps_result.scalars.return_value.all.return_value = [dep]
    ext_asset_result = MagicMock()
    ext_asset_result.scalar_one_or_none.return_value = external_asset
    mock_db.execute = AsyncMock(side_effect=[deps_result, ext_asset_result])

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_cross_host_dependency_map("token", [str(id1)])

    assert result["edges"][0]["internal"] is False
    assert len(result["external_dependencies"]) == 1
    assert result["external_dependencies"][0]["name"] == "external-db"


@pytest.mark.asyncio
async def test_kernel_eol_al2_detected():
    """get_kernel_eol_status correctly identifies Amazon Linux 2 kernel."""
    from app.mcp_tools.planning_context import get_kernel_eol_status

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, meta={"kernel_version": "5.10.68-62.173.amzn2.x86_64"})

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [asset]
    mock_db.execute = AsyncMock(return_value=assets_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_kernel_eol_status("token", [str(asset.id)])

    assert len(result) == 1
    assert result[0]["eol_date"] == "2025-06-30"
    assert result[0]["supported"] is False  # EOL was 2025-06-30, today is 2026-07-02


@pytest.mark.asyncio
async def test_kernel_eol_515_future():
    """get_kernel_eol_status returns future EOL for 5.15 kernel."""
    from app.mcp_tools.planning_context import get_kernel_eol_status

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, meta={"kernel_version": "5.15.0-89-generic"})

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [asset]
    mock_db.execute = AsyncMock(return_value=assets_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_kernel_eol_status("token", [str(asset.id)])

    # 5.15 EOL is 2026-10-31; today is 2026-07-02 -> still supported
    assert result[0]["eol_date"] == "2026-10-31"
    assert result[0]["supported"] is True
    assert result[0]["days_until_eol"] > 0


@pytest.mark.asyncio
async def test_kernel_eol_unknown_version():
    """get_kernel_eol_status returns None fields for unrecognized kernel."""
    from app.mcp_tools.planning_context import get_kernel_eol_status

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, meta={"kernel_version": "3.10.0-custom"})

    mock_db = AsyncMock()
    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = [asset]
    mock_db.execute = AsyncMock(return_value=assets_result)

    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.mcp_tools.planning_context._auth", return_value=(user, mock_db, mock_db_cm)):
        result = await get_kernel_eol_status("token", [str(asset.id)])

    assert result[0]["eol_date"] is None
    assert result[0]["supported"] is None
    assert result[0]["days_until_eol"] is None
