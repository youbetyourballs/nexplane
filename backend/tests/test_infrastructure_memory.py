"""
Tests for GET /infrastructure-memory
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.infrastructure_memory import router as infra_memory_router
from app.routers import current_user
from app.database import get_db


def _make_user(org_id=None):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.organization_id = org_id or uuid.uuid4()
    return user


def _make_asset(org_id, name="prod-db", criticality="high", owner=None, why_exists=None):
    a = MagicMock()
    a.id = uuid.uuid4()
    a.organization_id = org_id
    a.name = name
    a.asset_type = MagicMock()
    a.asset_type.value = "database"
    a.environment = MagicMock()
    a.environment.value = "prod"
    a.criticality = MagicMock()
    a.criticality.value = criticality
    a.asset_metadata = {}
    if owner:
        a.asset_metadata["owner"] = owner
    if why_exists:
        a.asset_metadata["why_exists"] = why_exists
    return a


def _make_db(assets, total=None):
    """Build an AsyncMock DB that returns the given assets list."""
    db = AsyncMock()

    count_result = MagicMock()
    count_result.scalar_one.return_value = total if total is not None else len(assets)

    assets_result = MagicMock()
    assets_result.scalars.return_value.all.return_value = assets

    # First execute call is the count, second is the actual rows
    db.execute = AsyncMock(side_effect=[count_result, assets_result])
    return db


def _make_app(user, db):
    app = FastAPI()
    app.include_router(infra_memory_router)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.asyncio
async def test_list_infrastructure_memory_returns_200():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    assets = [_make_asset(org_id, "alpha"), _make_asset(org_id, "beta")]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/infrastructure-memory")

    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_list_infrastructure_memory_pagination_shape():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    assets = [_make_asset(org_id, f"asset-{i}") for i in range(2)]
    db = _make_db(assets, total=10)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/infrastructure-memory?page=1&page_size=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) <= 2


@pytest.mark.asyncio
async def test_list_infrastructure_memory_search_term_in_results():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    # Only the matching asset is returned (DB filtering is mocked — simulates a real filtered response)
    matching = _make_asset(org_id, name="payments-db", why_exists="handles payments")
    db = _make_db([matching])

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/infrastructure-memory?q=payments")

    assert resp.status_code == 200
    data = resp.json()
    # All returned items should contain the search term somewhere in name/owner/why_exists
    for item in data["items"]:
        term_found = (
            "payments" in (item.get("name") or "").lower()
            or "payments" in (item.get("owner") or "").lower()
            or "payments" in (item.get("why_exists") or "").lower()
        )
        assert term_found, f"Search term not found in item: {item}"


@pytest.mark.asyncio
async def test_list_infrastructure_memory_org_scoped():
    """Response returns 200 and items belong to the authed org (shape check)."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    assets = [_make_asset(org_id, "my-server")]
    db = _make_db(assets)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/infrastructure-memory")

    assert resp.status_code == 200
    data = resp.json()
    # All returned item IDs should be from our mocked asset set
    returned_ids = {item["id"] for item in data["items"]}
    expected_ids = {str(a.id) for a in assets}
    assert returned_ids == expected_ids
