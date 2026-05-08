import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.routers.smoke_tests import cleanup_preview, cleanup_inventory


def _make_mock_asset(asset_id: str, name: str) -> MagicMock:
    a = MagicMock()
    a.id = asset_id
    a.name = name
    a.asset_type = MagicMock()
    a.asset_type.value = "server"
    a.created_at = MagicMock()
    a.created_at.isoformat.return_value = "2026-05-08T10:00:00"
    return a


def _make_mock_preview_db(assets: list) -> AsyncMock:
    """Mock DB for cleanup_preview (uses SELECT + scalars)."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = assets
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    return db


def _make_mock_inventory_db(rowcount: int) -> AsyncMock:
    """Mock DB for cleanup_inventory (uses bulk DELETE + rowcount)."""
    mock_result = MagicMock()
    mock_result.rowcount = rowcount
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    return db


def _make_mock_user(org_id: str = "org-1") -> MagicMock:
    user = MagicMock()
    user.organization_id = org_id
    return user


def test_cleanup_preview_returns_matching_assets():
    assets = [
        _make_mock_asset("a1", "nexplane-smoke-test-01"),
        _make_mock_asset("a2", "nexplane-smoke-ec2"),
    ]
    db = _make_mock_preview_db(assets)
    user = _make_mock_user()

    result = asyncio.run(cleanup_preview(user=user, db=db))

    assert result["count"] == 2
    names = [a["name"] for a in result["assets"]]
    assert "nexplane-smoke-test-01" in names
    assert "nexplane-smoke-ec2" in names


def test_cleanup_preview_returns_empty_when_no_matches():
    db = _make_mock_preview_db([])
    user = _make_mock_user()

    result = asyncio.run(cleanup_preview(user=user, db=db))

    assert result["count"] == 0
    assert result["assets"] == []


def test_cleanup_inventory_deletes_all_matching_assets():
    db = _make_mock_inventory_db(rowcount=2)
    user = _make_mock_user()

    result = asyncio.run(cleanup_inventory(user=user, db=db))

    assert result == {"deleted": 2}
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_cleanup_inventory_returns_zero_when_nothing_to_delete():
    db = _make_mock_inventory_db(rowcount=0)
    user = _make_mock_user()

    result = asyncio.run(cleanup_inventory(user=user, db=db))

    assert result == {"deleted": 0}
    db.commit.assert_called_once()
