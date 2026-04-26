import asyncio
import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.services.auth_service import hash_password

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
