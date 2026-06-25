"""
Tests for GET /impact-simulation
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.impact_simulation import router as impact_simulation_router
from app.routers import current_user
from app.database import get_db


def _make_user(org_id=None):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.organization_id = org_id or uuid.uuid4()
    return user


def _make_asset(org_id, asset_id=None):
    a = MagicMock()
    a.id = asset_id or uuid.uuid4()
    a.organization_id = org_id
    a.name = "payments-db"
    a.asset_type = MagicMock()
    a.asset_type.value = "database"
    a.environment = MagicMock()
    a.environment.value = "prod"
    a.criticality = MagicMock()
    a.criticality.value = "critical"
    a.asset_metadata = {"owner": "platform-team", "why_exists": "stores transactions"}
    return a


def _make_app(user, db):
    app = FastAPI()
    app.include_router(impact_simulation_router)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.asyncio
async def test_impact_simulation_valid_asset_returns_200():
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)
    asset = _make_asset(org_id, asset_id)

    db = AsyncMock()

    # First execute: select asset — returns it
    asset_result = MagicMock()
    asset_result.scalar_one_or_none.return_value = asset

    # Second execute: criticality enrichment for downstream — returns empty
    crit_result = MagicMock()
    crit_result.__iter__ = MagicMock(return_value=iter([]))

    # Third execute: recent CRs — returns empty
    cr_result = MagicMock()
    cr_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[asset_result, crit_result, cr_result])

    upstream = [{"id": str(uuid.uuid4()), "name": "config-service", "relationship_type": "depends_on", "direction": "upstream"}]
    downstream = [{"id": str(uuid.uuid4()), "name": "api-gateway", "relationship_type": "depends_on", "direction": "downstream"}]

    with (
        patch("app.routers.impact_simulation.get_upstream", AsyncMock(return_value=upstream)),
        patch("app.routers.impact_simulation.get_downstream", AsyncMock(return_value=downstream)),
    ):
        app = _make_app(user, db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/impact-simulation?asset_id={asset_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert "asset" in data
    assert "upstream" in data
    assert "downstream" in data
    assert "downstream_risk" in data
    assert "recent_crs" in data
    assert "open_findings_count" in data
    assert data["asset"]["id"] == str(asset_id)


@pytest.mark.asyncio
async def test_impact_simulation_nonexistent_asset_returns_404():
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    user = _make_user(org_id)

    db = AsyncMock()
    not_found_result = MagicMock()
    not_found_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=not_found_result)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/impact-simulation?asset_id={asset_id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_impact_simulation_missing_asset_id_returns_422():
    org_id = uuid.uuid4()
    user = _make_user(org_id)
    db = AsyncMock()

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/impact-simulation")

    assert resp.status_code == 422
