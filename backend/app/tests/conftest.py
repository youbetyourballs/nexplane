import asyncio
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.services.auth_service import hash_password, create_access_token

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest_asyncio.fixture
async def org(db):
    o = Organization(id=uuid.uuid4(), name="Test Org")
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def admin_user(db, org):
    u = User(id=uuid.uuid4(), organization_id=org.id, email="admin@test.example",
             name="Admin", role=UserRole.admin, hashed_password=hash_password("test"))
    db.add(u)
    await db.flush()
    return u


@pytest_asyncio.fixture
async def approver_user(db, org):
    u = User(id=uuid.uuid4(), organization_id=org.id, email="approver@test.example",
             name="Approver", role=UserRole.approver, hashed_password=hash_password("test"))
    db.add(u)
    await db.flush()
    return u


@pytest_asyncio.fixture
async def prod_critical_asset(db, org):
    a = Asset(id=uuid.uuid4(), organization_id=org.id, name="Prod Critical App",
              asset_type=AssetType.application, environment=Environment.prod, criticality=Criticality.critical,
              asset_metadata={})
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def dev_low_asset(db, org):
    a = Asset(id=uuid.uuid4(), organization_id=org.id, name="Dev Low Asset",
              asset_type=AssetType.server, environment=Environment.dev, criticality=Criticality.low,
              asset_metadata={})
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def auth_client(db):
    """AsyncClient authenticated as a fresh admin user, with DB override."""
    org = Organization(id=uuid.uuid4(), name=f"Auth Test Org {uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()

    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=f"auth-admin-{uuid.uuid4().hex[:8]}@test.example",
        name="Auth Admin",
        role=UserRole.admin,
        hashed_password=hash_password("testpass"),
    )
    db.add(user)
    await db.flush()

    token = create_access_token(str(user.id))

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── Vulnerability fixtures ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session(db):
    """Alias for db, used by vulnerability tests."""
    return db


@pytest_asyncio.fixture
async def test_org(db):
    o = Organization(id=uuid.uuid4(), name=f"Vuln Test Org {uuid.uuid4().hex[:6]}")
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def test_asset(db, test_org):
    a = Asset(
        id=uuid.uuid4(),
        organization_id=test_org.id,
        name="Test Asset",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={},
    )
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def test_asset_with_ip(db, test_org):
    a = Asset(
        id=uuid.uuid4(),
        organization_id=test_org.id,
        name="IP Asset",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={"ip_address": "10.0.1.50"},
    )
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def test_asset_with_hostname(db, test_org):
    a = Asset(
        id=uuid.uuid4(),
        organization_id=test_org.id,
        name="Hostname Asset",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={"hostname": "web-prod-01"},
    )
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def test_asset_with_software_metadata(db, test_org):
    a = Asset(
        id=uuid.uuid4(),
        organization_id=test_org.id,
        name="Software Asset",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={
            "software": [
                {"package": "openssl", "installed_version": "3.0.2", "os": "Ubuntu 22.04"}
            ]
        },
    )
    db.add(a)
    await db.flush()
    return a


@pytest_asyncio.fixture
async def test_finding(db, test_org):
    from app.models.vulnerability import VulnerabilityFinding
    f = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id=f"QID-{uuid.uuid4().hex[:8]}",
        source="webhook",
        finding_type="cve",
        severity="critical",
        cve_id="CVE-2024-1234",
        title="Test CVE Finding",
    )
    db.add(f)
    await db.flush()
    return f


@pytest_asyncio.fixture
async def test_policy(db, test_org):
    from app.models.vulnerability import RemediationPolicy
    p = RemediationPolicy(
        organization_id=test_org.id,
        name="Test Policy",
        match_finding_type="cve",
        action_type="patch_packages",
        approval_level="require_approval",
        priority=10,
        enabled=True,
    )
    db.add(p)
    await db.flush()
    return p


@pytest_asyncio.fixture
async def test_unmatched_finding(db, test_org):
    from app.models.vulnerability import VulnerabilityFinding
    f = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id=f"UNMATCHED-{uuid.uuid4().hex[:8]}",
        source="webhook",
        finding_type="cve",
        severity="high",
        title="Unmatched Finding",
        target_ip="10.0.1.50",
        target_hostname="web-prod-01",
    )
    db.add(f)
    await db.flush()
    return f


@pytest_asyncio.fixture
async def test_finding_with_overdue_sla(db, test_org):
    from datetime import datetime, timezone, timedelta
    from app.models.vulnerability import VulnerabilityFinding, RemediationSLA
    f = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id=f"OVERDUE-{uuid.uuid4().hex[:8]}",
        source="webhook",
        finding_type="cve",
        severity="critical",
        title="Overdue Finding",
    )
    db.add(f)
    await db.flush()
    sla = RemediationSLA(
        organization_id=test_org.id,
        finding_id=f.id,
        severity="critical",
        sla_hours=72,
        due_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(sla)
    await db.flush()
    return {"finding": f, "sla": sla}


@pytest_asyncio.fixture
async def client(db):
    """Unauthenticated AsyncClient with DB override."""
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def auth_client(db, test_org):
    """AsyncClient authenticated as a fresh admin user within test_org, with DB override.
    Overrides the base conftest auth_client to share the same org as test_org/test_finding fixtures.
    """
    user = User(
        id=uuid.uuid4(),
        organization_id=test_org.id,
        email=f"vuln-admin-{uuid.uuid4().hex[:8]}@test.example",
        name="Vuln Admin",
        role=UserRole.admin,
        hashed_password=hash_password("testpass"),
    )
    db.add(user)
    await db.flush()

    token = create_access_token(str(user.id))

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def mock_webhook_secret(monkeypatch):
    """Patch settings.WEBHOOK_SECRET for webhook tests."""
    import app.routers.vulnerability as vuln_router_module
    import app.config as config_module
    monkeypatch.setenv("WEBHOOK_SECRET", "test-webhook-secret")
    # Also patch the already-instantiated settings object directly
    object.__setattr__(config_module.settings, "WEBHOOK_SECRET", "test-webhook-secret")
    yield "test-webhook-secret"
    object.__setattr__(config_module.settings, "WEBHOOK_SECRET", "changeme")


# ── End vulnerability fixtures ───────────────────────────────────────────────

def make_cr(org_id, requester_id, change_type=ChangeType.dns_update, target_ids=None, desired_outcome=None):
    return ChangeRequest(
        id=uuid.uuid4(),
        organization_id=org_id,
        requester_id=requester_id,
        title="Test Change",
        description="",
        change_type=change_type,
        target_asset_ids=[str(t) for t in (target_ids or [])],
        desired_outcome=desired_outcome or {"rollback_strategy": "restore_previous_record"},
        status=ChangeRequestStatus.draft,
    )
