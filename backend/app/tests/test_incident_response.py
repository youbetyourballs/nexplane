"""Tests for the Incident Response API endpoints."""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, get_db
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.ir_playbook_template import IRPlaybookTemplate
from app.services.auth_service import hash_password, create_access_token

pytestmark = pytest.mark.anyio


@pytest_asyncio.fixture
async def org(db):
    o = Organization(id=uuid.uuid4(), name=f"IR Test Org {uuid.uuid4().hex[:6]}")
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def client(db, org):
    """Unauthenticated client."""
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def authed_client(db, org):
    """Authenticated as a regular (non-IR) user."""
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"user-{uuid.uuid4().hex[:8]}@test.example",
        name="Regular User", role=UserRole.security_operator,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()
    token = create_access_token(str(user.id))

    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def ir_client(db, org):
    """Authenticated as an IR responder."""
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"ir-{uuid.uuid4().hex[:8]}@test.example",
        name="IR Responder", role=UserRole.ir_responder,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()
    token = create_access_token(str(user.id))

    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def db_asset_id(db, org):
    """Create an asset and return its UUID string."""
    asset = Asset(
        id=uuid.uuid4(), organization_id=org.id, name="Test IR Asset",
        asset_type=AssetType.server, environment=Environment.prod,
        criticality=Criticality.high, asset_metadata={},
    )
    db.add(asset)
    await db.flush()
    return str(asset.id)


@pytest_asyncio.fixture(autouse=True)
async def seed_templates(db):
    """Seed the four playbook templates needed for tests."""
    templates = [
        IRPlaybookTemplate(
            id=uuid.uuid4(), playbook_type="isolate_host",
            display_name="Host Isolation",
            description="Isolate a compromised host via agent-side firewall rules",
            default_parameters={"management_cidr": "10.0.0.0/8"},
            ir_auto_approve=False,
        ),
        IRPlaybookTemplate(
            id=uuid.uuid4(), playbook_type="lockdown_account",
            display_name="Account Lockdown",
            description="Disable a user across all identity connectors simultaneously",
            default_parameters={},
            ir_auto_approve=False,
        ),
        IRPlaybookTemplate(
            id=uuid.uuid4(), playbook_type="phishing_response",
            display_name="Phishing Response",
            description="Block sender domain, reset passwords, revoke sessions, re-enroll MFA",
            default_parameters={},
            ir_auto_approve=False,
        ),
        IRPlaybookTemplate(
            id=uuid.uuid4(), playbook_type="preserve_evidence",
            display_name="Evidence Preservation",
            description="Collect forensic artifacts from host before remediation",
            default_parameters={"include_memory_dump": False},
            ir_auto_approve=False,
        ),
    ]
    from sqlalchemy import select
    for t in templates:
        existing = await db.execute(
            select(IRPlaybookTemplate).where(
                IRPlaybookTemplate.playbook_type == t.playbook_type
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(t)
    await db.flush()


async def test_get_ir_templates_unauthenticated(client: AsyncClient):
    """Unauthenticated requests must be rejected."""
    resp = await client.get("/api/ir/templates")
    assert resp.status_code == 401


async def test_get_ir_templates_returns_four(authed_client: AsyncClient):
    """Returns all four seeded playbook templates."""
    resp = await authed_client.get("/api/ir/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    types = {t["playbook_type"] for t in data}
    assert types == {"isolate_host", "lockdown_account", "phishing_response", "preserve_evidence"}


async def test_ir_template_fields(authed_client: AsyncClient):
    """Each template has required fields."""
    resp = await authed_client.get("/api/ir/templates")
    assert resp.status_code == 200
    for t in resp.json():
        assert "id" in t
        assert "playbook_type" in t
        assert "display_name" in t
        assert "default_parameters" in t
        assert "ir_auto_approve" in t


async def test_instantiate_ir_playbook_requires_ir_role(authed_client: AsyncClient):
    """Non-IR-responder users cannot instantiate IR playbooks."""
    resp = await authed_client.post("/api/ir/instantiate", json={
        "playbook_type": "isolate_host",
        "parameters": {"asset_id": "00000000-0000-0000-0000-000000000001",
                       "management_cidr": "10.0.0.0/8", "reason": "test"},
    })
    assert resp.status_code == 403


async def test_instantiate_unknown_playbook_type(ir_client: AsyncClient):
    """Unknown playbook type returns 404."""
    resp = await ir_client.post("/api/ir/instantiate", json={
        "playbook_type": "nonexistent_type",
        "parameters": {},
    })
    assert resp.status_code == 404


async def test_instantiate_isolate_host_creates_cr(ir_client: AsyncClient, db_asset_id: str):
    """Instantiating isolate_host creates a change request with incident_response=True."""
    resp = await ir_client.post("/api/ir/instantiate", json={
        "playbook_type": "isolate_host",
        "parameters": {
            "asset_id": db_asset_id,
            "management_cidr": "10.0.0.0/8",
            "reason": "Lateral movement detected",
        },
    })
    assert resp.status_code == 201
    cr = resp.json()
    # incident_response flag and playbook type are stored in desired_outcome
    assert cr["desired_outcome"].get("ir_playbook_type") == "isolate_host"
    assert cr["status"] in ("approved", "awaiting_approval", "draft")


async def test_get_ir_bundles_empty(ir_client: AsyncClient):
    """Empty bundles list returns 200 with empty array."""
    resp = await ir_client.get("/api/ir/bundles")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_ir_bundle_not_found(ir_client: AsyncClient):
    """Non-existent bundle returns 404."""
    resp = await ir_client.get("/api/ir/bundles/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


async def test_get_ir_bundle_download_url_not_found(ir_client: AsyncClient):
    """Download URL for non-existent bundle returns 404."""
    resp = await ir_client.get("/api/ir/bundles/00000000-0000-0000-0000-000000000099/download-url")
    assert resp.status_code == 404
