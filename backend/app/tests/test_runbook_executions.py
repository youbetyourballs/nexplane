"""
Tests for execution detail, resume (human checkpoint), and abort endpoints.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.database import get_db
from app.main import app
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.services.auth_service import hash_password, create_access_token


@pytest_asyncio.fixture
async def client(db):
    """Authenticated client for runbook execution tests."""
    org = Organization(id=uuid.uuid4(), name=f"RbExec Org {uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"rbexec-admin-{uuid.uuid4().hex[:8]}@test.example",
        name="Exec Admin", role=UserRole.admin,
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


async def _create_and_trigger(client: AsyncClient) -> tuple[str, str]:
    """Helper: create a runbook with a human checkpoint and trigger it."""
    resp = await client.post("/api/runbooks", json={
        "name": "Checkpoint Runbook",
        "tags": [],
        "steps": [
            {
                "step_number": 1,
                "name": "Approval Gate",
                "type": "human_checkpoint",
                "prompt": "Please approve.",
                "required_role": "admin",
                "timeout_hours": 24,
                "on_timeout": "abort",
                "on_failure": "abort",
                "parallel_steps": [],
            }
        ],
    })
    rb_id = resp.json()["id"]
    exec_resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    return rb_id, exec_resp.json()["id"]


@pytest.mark.asyncio
async def test_get_execution(client: AsyncClient):
    _, exec_id = await _create_and_trigger(client)
    resp = await client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == exec_id
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_abort_execution(client: AsyncClient):
    _, exec_id = await _create_and_trigger(client)
    resp = await client.post(f"/api/executions/{exec_id}/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_resume_execution_wrong_status_fails(client: AsyncClient):
    """Resume should fail if execution is not waiting_human."""
    _, exec_id = await _create_and_trigger(client)
    # Execution is "running", not "waiting_human" yet (poller hasn't ticked)
    resp = await client.post(f"/api/executions/{exec_id}/resume", json={
        "step_number": 1, "action": "resume"
    })
    assert resp.status_code == 409


@pytest_asyncio.fixture
async def other_org_client(db):
    """Authenticated client for a different org."""
    org2 = Organization(id=uuid.uuid4(), name=f"Other Org {uuid.uuid4().hex[:6]}")
    db.add(org2)
    await db.flush()
    user2 = User(
        id=uuid.uuid4(), organization_id=org2.id,
        email=f"other-{uuid.uuid4().hex[:8]}@test.example",
        name="Other Admin", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user2)
    await db.flush()
    token = create_access_token(str(user2.id))

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
async def test_get_execution_wrong_org_fails(client: AsyncClient, other_org_client: AsyncClient):
    """Execution from org A must not be visible to org B."""
    _, exec_id = await _create_and_trigger(client)
    resp = await other_org_client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 404
