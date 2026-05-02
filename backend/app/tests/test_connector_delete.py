import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.connector import Connector, ConnectorType, ConnectorStatus
from app.services.auth_service import hash_password, create_access_token


async def _setup_test_data(db: AsyncSession):
    """Create org, user, and connector; return (token, connector_id)."""
    org = Organization(id=uuid.uuid4(), name="Delete Test Org")
    db.add(org)
    await db.flush()

    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=f"operator-{uuid.uuid4().hex[:8]}@test.example",
        name="Operator",
        role=UserRole.admin,
        hashed_password=hash_password("testpass"),
    )
    db.add(user)
    await db.flush()

    connector = Connector(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="Test Connector",
        connector_type=ConnectorType.aws_mock,
        status=ConnectorStatus.active,
        scoped_permissions={},
    )
    db.add(connector)
    await db.flush()

    token = create_access_token(str(user.id))
    return token, str(connector.id)


@pytest.mark.asyncio
async def test_delete_connector_returns_204(db: AsyncSession):
    token, connector_id = await _setup_test_data(db)

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                f"/connectors/{connector_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 204
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_delete_connector_removes_from_list(db: AsyncSession):
    token, connector_id = await _setup_test_data(db)

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.delete(
                f"/connectors/{connector_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            list_resp = await client.get(
                "/connectors",
                headers={"Authorization": f"Bearer {token}"},
            )
        ids = [c["id"] for c in list_resp.json()]
        assert connector_id not in ids
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_delete_connector_404_for_wrong_id(db: AsyncSession):
    token, _ = await _setup_test_data(db)

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                f"/connectors/{uuid.uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
