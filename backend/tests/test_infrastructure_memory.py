# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Tests for infrastructure memory endpoints and service functions.

Covers:
  - Legacy GET /infrastructure-memory (asset search)
  - GET /memory/provenance/{asset_id}  (why does this asset/config exist?)
  - GET /memory/dependencies/{asset_id} (what depends on this asset?)
  - GET /memory/deletion-check/{asset_id} (can this be safely deleted?)
  - GET /memory/timeline (what changed in scope/time window?)
  - POST /memory/query (NL dispatcher)
  - parse_query_intent unit tests
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.infrastructure_memory import router as infra_memory_router, _legacy_router
from app.routers import current_user
from app.database import get_db
from app.services.infrastructure_memory_service import parse_query_intent


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
    app.include_router(_legacy_router)
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


# ===========================================================================
# parse_query_intent unit tests — no DB, no HTTP
# ===========================================================================

class TestParseQueryIntent:
    def test_port_provenance(self):
        result = parse_query_intent("Why is port 8443 open on prod-gateway-01?")
        assert result["intent"] == "provenance"
        assert result["entities"]["port"] == "8443"

    def test_firewall_approval_cidr(self):
        result = parse_query_intent("Who approved the firewall rule allowing 10.2.0.0/16?")
        assert result["intent"] == "approval_search"
        assert result["entities"]["cidr"] == "10.2.0.0/16"

    def test_cert_dependents(self):
        result = parse_query_intent("Which applications depend on cert wildcard.acme.internal?")
        assert result["intent"] == "dependents"

    def test_deletion_check(self):
        result = parse_query_intent("Can prod-worker-07 be deleted safely?")
        assert result["intent"] == "deletion_check"

    def test_timeline_query(self):
        result = parse_query_intent("What changed in the us-east-1 VPC yesterday?")
        assert result["intent"] == "timeline"
        assert result["entities"]["region"] == "us-east-1"

    def test_show_changes(self):
        result = parse_query_intent("Show me everything that changed last week")
        assert result["intent"] == "timeline"

    def test_approval_without_cidr(self):
        result = parse_query_intent("Who approved this security group change?")
        assert result["intent"] == "approval_search"


# ===========================================================================
# Service function unit tests — mock DB, no HTTP
# ===========================================================================

def _async_result(value):
    """Return a coroutine that yields value, for use as AsyncMock return_value."""
    m = MagicMock()
    m.scalar_one_or_none.return_value = value
    m.scalar_one.return_value = value if isinstance(value, int) else 0
    m.scalars.return_value.all.return_value = []
    m.all.return_value = []
    return m


@pytest.mark.asyncio
async def test_get_asset_provenance_asset_not_found():
    from app.services.infrastructure_memory_service import get_asset_provenance

    db = AsyncMock()
    db.execute.return_value = _async_result(None)

    result = await get_asset_provenance(db, uuid.uuid4(), uuid.uuid4())
    assert result["error"] == "asset_not_found"


@pytest.mark.asyncio
async def test_get_asset_provenance_returns_history():
    from app.services.infrastructure_memory_service import get_asset_provenance

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    # Mock asset
    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_asset.organization_id = org_id
    mock_asset.name = "prod-gateway-01"
    mock_asset.asset_type.value = "server"
    mock_asset.asset_metadata = {
        "owner": "infra-team",
        "why_exists": "edge proxy",
        "open_ports": [8443],
    }

    db = AsyncMock()
    # execute calls: asset lookup, CR list, approvals, plans
    asset_result = MagicMock()
    asset_result.scalar_one_or_none.return_value = mock_asset

    cr_result = MagicMock()
    cr_result.all.return_value = []  # no CRs

    ap_result = MagicMock()
    ap_result.all.return_value = []

    plan_result = MagicMock()
    plan_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[asset_result, cr_result, ap_result, plan_result])

    result = await get_asset_provenance(db, org_id, asset_id)
    assert result["asset_name"] == "prod-gateway-01"
    assert result["owner"] == "infra-team"
    assert result["open_ports"] == [8443]
    assert result["change_history"] == []


@pytest.mark.asyncio
async def test_can_asset_be_deleted_no_dependents():
    from app.services.infrastructure_memory_service import can_asset_be_deleted

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_asset.name = "prod-worker-07"
    mock_asset.asset_type.value = "server"
    mock_asset.criticality.value = "low"
    mock_asset.asset_metadata = {}

    asset_result = MagicMock()
    asset_result.scalar_one_or_none.return_value = mock_asset

    dep_count_result = MagicMock()
    dep_count_result.scalar_one.return_value = 0

    rel_count_result = MagicMock()
    rel_count_result.scalar_one.return_value = 0

    last_cr_result = MagicMock()
    last_cr_result.scalar_one_or_none.return_value = None

    snap_result = MagicMock()
    snap_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        asset_result, dep_count_result, rel_count_result, last_cr_result, snap_result
    ])

    result = await can_asset_be_deleted(db, org_id, asset_id)
    assert result["safe"] is True
    assert result["dependent_count"] == 0
    assert result["blocking_reasons"] == []


@pytest.mark.asyncio
async def test_can_asset_be_deleted_blocked_by_dependents():
    from app.services.infrastructure_memory_service import can_asset_be_deleted

    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_asset.name = "prod-db-01"
    mock_asset.asset_type.value = "database"
    mock_asset.criticality.value = "critical"
    mock_asset.asset_metadata = {}

    asset_result = MagicMock()
    asset_result.scalar_one_or_none.return_value = mock_asset

    dep_count_result = MagicMock()
    dep_count_result.scalar_one.return_value = 3

    rel_count_result = MagicMock()
    rel_count_result.scalar_one.return_value = 1

    last_cr_result = MagicMock()
    last_cr_result.scalar_one_or_none.return_value = None

    snap_result = MagicMock()
    snap_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        asset_result, dep_count_result, rel_count_result, last_cr_result, snap_result
    ])

    result = await can_asset_be_deleted(db, org_id, asset_id)
    assert result["safe"] is False
    assert result["dependent_count"] == 4
    assert result["requires_approval"] is True
    assert len(result["blocking_reasons"]) >= 1


@pytest.mark.asyncio
async def test_get_timeline_summary_basic():
    from app.services.infrastructure_memory_service import get_timeline_summary

    org_id = uuid.uuid4()

    mock_cr = MagicMock()
    mock_cr.id = uuid.uuid4()
    mock_cr.change_type.value = "security_group_update"
    mock_cr.title = "Add rule for 10.2.0.0/16"
    mock_cr.status.value = "completed"
    mock_cr.source = None
    mock_cr.created_at = datetime.now(timezone.utc)
    mock_cr.applied_at = datetime.now(timezone.utc)
    mock_cr.stateful_approved_at = datetime.now(timezone.utc)
    mock_cr.target_asset_ids = [str(uuid.uuid4())]
    mock_cr.verification_checks = []

    cr_result = MagicMock()
    cr_result.scalars.return_value.all.return_value = [mock_cr]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=cr_result)

    result = await get_timeline_summary(
        db=db,
        org_id=org_id,
        since=datetime.now(timezone.utc),
    )
    assert result["total"] == 1
    assert result["approved_count"] == 1
    assert result["auto_remediation_count"] == 0
    assert len(result["changes"]) == 1


@pytest.mark.asyncio
async def test_search_crs_by_parameter_cidr():
    from app.services.infrastructure_memory_service import search_crs_by_parameter

    org_id = uuid.uuid4()

    mock_user = MagicMock()
    mock_user.email = "admin@example.com"

    mock_cr = MagicMock()
    mock_cr.id = uuid.uuid4()
    mock_cr.change_type.value = "security_group_update"
    mock_cr.title = "Allow 10.2.0.0/16 ingress"
    mock_cr.description = "Firewall rule for VPN subnet"
    mock_cr.status.value = "completed"
    mock_cr.applied_at = datetime.now(timezone.utc)
    mock_cr.stateful_approved_at = datetime.now(timezone.utc)
    mock_cr.artifact_refs = None
    mock_cr.verification_checks = []
    mock_cr.target_asset_ids = []

    cr_result = MagicMock()
    cr_result.all.return_value = [(mock_cr, mock_user)]

    ap_result = MagicMock()
    ap_result.all.return_value = []

    plan_result = MagicMock()
    plan_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cr_result, ap_result, plan_result])

    results = await search_crs_by_parameter(db, org_id, "10.2.0.0/16")
    assert len(results) == 1
    assert "10.2.0.0/16" in results[0]["title"]


@pytest.mark.asyncio
async def test_get_asset_dependents_not_found():
    from app.services.infrastructure_memory_service import get_asset_dependents

    db = AsyncMock()
    not_found_result = MagicMock()
    not_found_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=not_found_result)

    result = await get_asset_dependents(db, uuid.uuid4(), uuid.uuid4())
    assert result["error"] == "asset_not_found"


@pytest.mark.asyncio
async def test_get_asset_dependents_cert_with_dependents():
    from app.services.infrastructure_memory_service import get_asset_dependents

    org_id = uuid.uuid4()
    cert_asset_id = uuid.uuid4()
    app_asset_id = uuid.uuid4()

    mock_cert = MagicMock()
    mock_cert.id = cert_asset_id
    mock_cert.name = "wildcard.acme.internal"
    mock_cert.asset_type.value = "server"
    mock_cert.asset_metadata = {
        "owner": "platform-team",
        "cert_expiry": "2026-12-01T00:00:00Z",
        "cert_subject": "*.acme.internal",
    }

    mock_app = MagicMock()
    mock_app.id = app_asset_id
    mock_app.name = "api-service"
    mock_app.asset_type.value = "application"
    mock_app.environment.value = "prod"
    mock_app.asset_metadata = {"owner": "backend-team"}

    mock_dep = MagicMock()
    mock_dep.dependency_type = "uses_cert"
    mock_dep.dep_metadata = {}

    cert_result = MagicMock()
    cert_result.scalar_one_or_none.return_value = mock_cert

    dep_result = MagicMock()
    dep_result.all.return_value = [(mock_dep, mock_app)]

    rel_result = MagicMock()
    rel_result.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cert_result, dep_result, rel_result])

    result = await get_asset_dependents(db, org_id, cert_asset_id)
    assert result["dependent_count"] == 1
    assert result["dependents"][0]["asset_name"] == "api-service"
    assert result["owner"] == "platform-team"
    assert result["cert_expiry"] == "2026-12-01T00:00:00Z"
    assert result["recommendation"] is not None  # expiry in future → valid message


# ===========================================================================
# HTTP endpoint smoke tests for new /memory/* routes
# ===========================================================================

@pytest.mark.asyncio
async def test_memory_provenance_404():
    """Returns 404 when asset is not found."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)

    db = AsyncMock()
    not_found = MagicMock()
    not_found.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=not_found)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/memory/provenance/{uuid.uuid4()}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_memory_deletion_check_200():
    """Deletion check returns 200 with expected keys."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)

    mock_asset = MagicMock()
    mock_asset.id = uuid.uuid4()
    mock_asset.name = "prod-worker-07"
    mock_asset.asset_type.value = "server"
    mock_asset.criticality.value = "low"
    mock_asset.asset_metadata = {}

    def _make_scalar_result(v):
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        r.scalar_one.return_value = 0
        r.scalars.return_value.all.return_value = []
        return r

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _make_scalar_result(mock_asset),  # asset lookup
        _make_scalar_result(0),           # dep count
        _make_scalar_result(0),           # rel count
        _make_scalar_result(None),        # last CR
        _make_scalar_result(None),        # snap CRs
    ])

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/memory/deletion-check/{mock_asset.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert "safe" in data
    assert "dependent_count" in data
    assert "requires_approval" in data
    assert "blocking_reasons" in data


@pytest.mark.asyncio
async def test_memory_timeline_200():
    """Timeline returns 200 with expected summary keys."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)

    cr_result = MagicMock()
    cr_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=cr_result)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/memory/timeline")

    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "approved_count" in data
    assert "rollback_count" in data
    assert "changes" in data


@pytest.mark.asyncio
async def test_memory_query_timeline_intent():
    """POST /memory/query routes timeline intent correctly."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)

    cr_result = MagicMock()
    cr_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=cr_result)

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/memory/query",
            json={"query": "What changed in the us-east-1 VPC yesterday?"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "timeline"
    assert "result" in data


@pytest.mark.asyncio
async def test_memory_query_approval_search():
    """POST /memory/query routes approval_search intent and returns results."""
    org_id = uuid.uuid4()
    user = _make_user(org_id)

    cr_result = MagicMock()
    cr_result.all.return_value = []

    ap_result = MagicMock()
    ap_result.all.return_value = []

    plan_result = MagicMock()
    plan_result.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[cr_result, ap_result, plan_result])

    app = _make_app(user, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/memory/query",
            json={"query": "Who approved the firewall rule allowing 10.2.0.0/16?"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "approval_search"
    assert "results" in data
