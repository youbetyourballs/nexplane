"""
Tests for runbook CRUD and trigger endpoints.
All tests use the shared `client` fixture (AsyncClient with overridden auth).
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services.auth_service import hash_password, create_access_token


@pytest_asyncio.fixture
async def client(db):
    """Authenticated client for runbook tests."""
    org = Organization(id=uuid.uuid4(), name=f"Runbook Org {uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"rb-admin-{uuid.uuid4().hex[:8]}@test.example",
        name="Runbook Admin", role=UserRole.admin,
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
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_list_runbooks_empty(client: AsyncClient):
    resp = await client.get("/api/runbooks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_runbook(client: AsyncClient):
    payload = {
        "name": "Test Runbook",
        "description": "A test",
        "tags": ["test"],
        "steps": [
            {
                "step_number": 1,
                "name": "Patch Fleet",
                "type": "change",
                "change_type": "patch_packages",
                "on_failure": "abort",
                "parallel_steps": [],
            }
        ],
    }
    resp = await client.post("/api/runbooks", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Runbook"
    assert data["version"] == 1
    assert data["is_seed"] is False
    assert len(data["steps"]) == 1
    return data["id"]


@pytest.mark.asyncio
async def test_get_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Get Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.get(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rb_id


@pytest.mark.asyncio
async def test_update_runbook_bumps_version(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Version Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.put(f"/api/runbooks/{rb_id}", json={"name": "Version Test v2"})
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["name"] == "Version Test v2"


@pytest.mark.asyncio
async def test_delete_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Delete Me", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 204
    resp2 = await client.get(f"/api/runbooks/{rb_id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_fork_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Original", "tags": ["onboarding"], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.post(f"/api/runbooks/{rb_id}/fork")
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Original (copy)"
    assert data["version"] == 1
    assert data["is_seed"] is False


@pytest.mark.asyncio
async def test_trigger_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Trigger Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {"env": "prod"}})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    assert data["runbook_version"] == 1
    assert data["context"] == {"env": "prod"}


@pytest.mark.asyncio
async def test_list_executions(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Exec List Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    resp = await client.get(f"/api/runbooks/{rb_id}/executions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_runbooks_tag_filter(client: AsyncClient):
    await client.post("/api/runbooks", json={"name": "Tagged", "tags": ["security"], "steps": []})
    await client.post("/api/runbooks", json={"name": "Untagged", "tags": [], "steps": []})
    resp = await client.get("/api/runbooks?tag=security")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert "Tagged" in names
    assert "Untagged" not in names


@pytest.mark.asyncio
async def test_delete_runbook_with_active_execution_fails(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Active Exec", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    resp = await client.delete(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 409
