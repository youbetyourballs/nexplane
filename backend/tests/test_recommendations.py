# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Tests for GET /recommendations
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.recommendations import router as recommendations_router
from app.routers import current_user
from app.database import get_db

REQUIRED_FIELDS = {"id", "asset_id", "asset_name", "rule", "title", "description", "priority", "action_link"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _make_user(org_id=None):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.organization_id = org_id or uuid.uuid4()
    return user


def _make_asset(org_id, name="test-asset", criticality="high", owner=None, why_exists=None):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = name
    a.asset_type = MagicMock()
    a.asset_type.value = "server"
    a.criticality = MagicMock()
    a.criticality.value = criticality
    a.asset_metadata = {}
    if owner:
        a.asset_metadata["owner"] = owner
    if why_exists:
        a.asset_metadata["why_exists"] = why_exists
    return a


def _make_db(assets, source_counts=None, target_counts=None):
    """
    Build an AsyncMock DB that returns assets + relationship count rows.
    The router does 3 executes: assets, source_counts, target_counts.
    """
    db = AsyncMock()

    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = assets

    # source counts: list of row-like objects with source_asset_id / cnt
    source_rows = []
    for asset_id, cnt in (source_counts or {}).items():
        row = MagicMock()
        row.source_asset_id = asset_id
        row.cnt = cnt
        source_rows.append(row)
    source_result = MagicMock()
    source_result.__iter__ = MagicMock(return_value=iter(source_rows))

    target_rows = []
    for asset_id, cnt in (target_counts or {}).items():
        row = MagicMock()
        row.target_asset_id = asset_id
        row.cnt = cnt
        target_rows.append(row)
    target_result = MagicMock()
    target_result.__iter__ = MagicMock(return_value=iter(target_rows))

    db.execute = AsyncMock(side_effect=[assets_result, source_result, target_result])
    return db


def _make_app(user, db):
    app = FastAPI()
    app.include_router(recommendations_router)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.asyncio
async def test_recommendations_returns_200():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    assets = [_make_asset(org_id)]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/recommendations")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_recommendations_empty_org_returns_empty_list():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    db = _make_db([])

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/recommendations")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_recommendations_items_have_required_fields():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    # Asset with no owner triggers a recommendation
    assets = [_make_asset(org_id, criticality="critical")]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/recommendations")

    data = resp.json()
    assert len(data) > 0
    for item in data:
        missing = REQUIRED_FIELDS - set(item.keys())
        assert not missing, f"Item missing fields: {missing}"


@pytest.mark.asyncio
async def test_recommendations_priority_values_are_valid():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    assets = [
        _make_asset(org_id, name="no-owner-critical", criticality="critical"),
        _make_asset(org_id, name="no-owner-medium", criticality="medium"),
    ]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/recommendations")

    data = resp.json()
    for item in data:
        assert item["priority"] in VALID_PRIORITIES, f"Unexpected priority: {item['priority']}"


@pytest.mark.asyncio
async def test_recommendations_sorted_by_priority():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    # critical + no owner → critical rec; medium + no owner → medium rec
    assets = [
        _make_asset(org_id, name="alpha-medium", criticality="medium"),
        _make_asset(org_id, name="beta-critical", criticality="critical"),
    ]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/recommendations")

    data = resp.json()
    if len(data) >= 2:
        for i in range(len(data) - 1):
            p_current = PRIORITY_ORDER[data[i]["priority"]]
            p_next = PRIORITY_ORDER[data[i + 1]["priority"]]
            assert p_current <= p_next, (
                f"Priority order violated at index {i}: "
                f"{data[i]['priority']} before {data[i+1]['priority']}"
            )
