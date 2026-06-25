"""
Tests for the asset graph router:
  GET  /assets/{id}/relationships
  POST /assets/{id}/relationships
  DELETE /assets/relationships/{rel_id}
  GET  /assets/{id}/graph

Uses FastAPI's dependency-override mechanism to avoid a live DB.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.asset_graph import router as asset_graph_router
from app.routers import current_user
from app.database import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org_id():
    return uuid.uuid4()


def _make_user(org_id):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.organization_id = org_id
    return user


def _make_asset(org_id, asset_id=None):
    a = MagicMock()
    a.id = asset_id or uuid.uuid4()
    a.organization_id = org_id
    a.name = "test-asset"
    a.asset_type = MagicMock()
    a.asset_type.value = "server"
    a.environment = MagicMock()
    a.environment.value = "prod"
    a.criticality = MagicMock()
    a.criticality.value = "high"
    return a


def _make_rel(org_id, source_id, target_id, rel_id=None):
    r = MagicMock()
    r.id = rel_id or uuid.uuid4()
    r.organization_id = org_id
    r.source_asset_id = source_id
    r.target_asset_id = target_id
    r.relationship_type = "depends_on"
    r.rel_metadata = {}
    r.created_by = None
    r.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return r


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(user, db_mock):
    app = FastAPI()
    app.include_router(asset_graph_router)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db_mock
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_relationships_returns_200():
    org_id = _make_org_id()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, asset_id)
    rel = _make_rel(org_id, asset_id, uuid.uuid4())

    db = AsyncMock()
    db.get = AsyncMock(return_value=asset)

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rel]
    db.execute = AsyncMock(return_value=execute_result)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/assets/{asset_id}/relationships")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["relationship_type"] == "depends_on"


@pytest.mark.asyncio
async def test_list_relationships_unknown_asset_returns_404():
    org_id = _make_org_id()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)  # asset not found

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/assets/{asset_id}/relationships")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_relationship_returns_201():
    org_id = _make_org_id()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    user = _make_user(org_id)
    source = _make_asset(org_id, source_id)
    target = _make_asset(org_id, target_id)
    rel = _make_rel(org_id, source_id, target_id)

    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, pk: source if pk == source_id else target)

    # No existing relationship
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=existing_result)

    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda r: None)

    # patch AssetRelationship constructor to return our mock rel
    with patch("app.routers.asset_graph.AssetRelationship", return_value=rel):
        app = _make_app(user, db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/assets/{source_id}/relationships",
                json={
                    "target_asset_id": str(target_id),
                    "relationship_type": "depends_on",
                    "rel_metadata": {},
                },
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["source_asset_id"] == str(source_id)
    assert data["target_asset_id"] == str(target_id)
    assert data["relationship_type"] == "depends_on"


@pytest.mark.asyncio
async def test_create_relationship_target_other_org_returns_404():
    org_id = _make_org_id()
    other_org_id = _make_org_id()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    user = _make_user(org_id)
    source = _make_asset(org_id, source_id)
    # target belongs to a different org
    target = _make_asset(other_org_id, target_id)

    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, pk: source if pk == source_id else target)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/assets/{source_id}/relationships",
            json={
                "target_asset_id": str(target_id),
                "relationship_type": "depends_on",
            },
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_relationship_returns_204():
    org_id = _make_org_id()
    rel_id = uuid.uuid4()
    user = _make_user(org_id)
    rel = _make_rel(org_id, uuid.uuid4(), uuid.uuid4(), rel_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=rel)
    db.delete = AsyncMock()
    db.commit = AsyncMock()

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/assets/relationships/{rel_id}")

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_relationship_not_found_returns_404():
    org_id = _make_org_id()
    rel_id = uuid.uuid4()
    user = _make_user(org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/assets/relationships/{rel_id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_graph_returns_correct_shape():
    org_id = _make_org_id()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, asset_id)
    rel = _make_rel(org_id, asset_id, uuid.uuid4())

    db = AsyncMock()
    db.get = AsyncMock(return_value=asset)

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rel]
    db.execute = AsyncMock(return_value=execute_result)

    mock_neighbors = [{"id": str(uuid.uuid4()), "name": "neighbor", "relationship_type": "depends_on", "direction": "downstream"}]

    with patch("app.routers.asset_graph.get_neighbors", AsyncMock(return_value=mock_neighbors)):
        app = _make_app(user, db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/assets/{asset_id}/graph")

    assert resp.status_code == 200
    data = resp.json()
    assert "asset" in data
    assert "neighbors" in data
    assert "edges" in data
    assert data["asset"]["id"] == str(asset_id)
    assert isinstance(data["neighbors"], list)
    assert isinstance(data["edges"], list)
