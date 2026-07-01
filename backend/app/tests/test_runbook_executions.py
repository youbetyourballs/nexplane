# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
        "auto_execute": True,
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


@pytest.mark.asyncio
async def test_runbook_has_auto_execute_field(db):
    """Runbook model must have an auto_execute bool field defaulting to False."""
    from app.models.runbook import Runbook

    org = Organization(id=uuid.uuid4(), name="AE Test Org")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"ae-{uuid.uuid4().hex[:8]}@test.example",
        name="AE User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()
    rb = Runbook(
        organization_id=org.id,
        name="AE Test Runbook",
        version=1,
        is_seed=False,
        created_by=user.id,
    )
    db.add(rb)
    await db.flush()
    assert rb.auto_execute is False


@pytest.mark.asyncio
async def test_trigger_blocked_when_auto_execute_false(client: AsyncClient):
    """Triggering a runbook with auto_execute=False must return 403."""
    # Create a runbook (auto_execute defaults to False)
    resp = await client.post("/api/runbooks", json={
        "name": "Disabled Runbook",
        "tags": [],
        "steps": [
            {"step_number": 1, "name": "Step 1", "type": "human_checkpoint",
             "prompt": "Approve?", "required_role": "admin",
             "timeout_hours": 1, "on_timeout": "abort", "on_failure": "abort",
             "parallel_steps": []}
        ],
    })
    assert resp.status_code == 201
    rb_id = resp.json()["id"]

    trigger_resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    assert trigger_resp.status_code == 403
    assert "auto-execution" in trigger_resp.json()["detail"].lower()


from app.services.change_plan_service import plan_cr, PlanResult
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus


@pytest.mark.asyncio
async def test_plan_cr_sets_status_to_planned(db):
    """plan_cr must set CR status to 'planned' and create a ChangePlan."""
    from app.models.change_plan import ChangePlan
    org = Organization(id=uuid.uuid4(), name=f"PlanCR Org {uuid.uuid4().hex[:4]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"plancr-{uuid.uuid4().hex[:8]}@test.example",
        name="PlanCR User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()

    cr = ChangeRequest(
        organization_id=org.id,
        requester_id=user.id,
        title="Test CR",
        description="Test",
        change_type=ChangeType.isolate_host,
        target_asset_ids=[],
        desired_outcome={},
        risk_level=RiskLevel.low,
        status=ChangeRequestStatus.draft,
        source="test",
    )
    db.add(cr)
    await db.flush()

    plan = await plan_cr(db, cr)
    assert cr.status == ChangeRequestStatus.planned
    assert plan is not None
    assert isinstance(plan, PlanResult)
    assert plan.plan is not None
    assert isinstance(plan.risk_level, str)
    assert isinstance(plan.risk_score, (int, float))
    assert isinstance(plan.risk_factors, list)
    assert isinstance(plan.warnings, list)


from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_create_and_execute_runbook_cr_drives_cr_to_approved(db):
    """
    create_and_execute_runbook_cr must create a CR, plan it, set it to
    approved, and fire the workflow — all without a commit in between.
    """
    from app.services.runbook_cr_bridge import create_and_execute_runbook_cr
    from app.models.runbook import RunbookExecution, Runbook

    org = Organization(id=uuid.uuid4(), name=f"Bridge Org {uuid.uuid4().hex[:4]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"bridge-{uuid.uuid4().hex[:8]}@test.example",
        name="Bridge User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()

    rb = Runbook(
        organization_id=org.id,
        name="Bridge Test Runbook",
        version=1,
        is_seed=False,
        created_by=user.id,
    )
    db.add(rb)
    await db.flush()

    execution = RunbookExecution(
        runbook_id=rb.id,
        runbook_version=1,
        runbook_snapshot={"organization_id": str(org.id), "steps": []},
        triggered_by=user.id,
        context={},
        status="running",
        current_step=1,
    )
    db.add(execution)
    await db.flush()

    step_def = {
        "step_number": 1,
        "name": "Isolate Host",
        "type": "change",
        "change_type": "isolate_host",
        "parameters": {},
        "asset_selector": None,
        "on_failure": "abort",
    }

    # Patch start_workflow so we don't actually launch a workflow task
    with patch(
        "app.services.runbook_cr_bridge.start_workflow",
        new_callable=AsyncMock,
        return_value="wf-test-123",
    ):
        cr = await create_and_execute_runbook_cr(db, execution, step_def)

    assert cr.status.value == "approved"
    assert cr.source == "runbook"


@pytest.mark.asyncio
async def test_force_trigger_blocked_for_non_admin(db):
    """Non-admins must not be able to force-trigger past a maintenance window."""
    org = Organization(id=uuid.uuid4(), name=f"Force Org {uuid.uuid4().hex[:4]}")
    db.add(org)
    await db.flush()
    non_admin = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"operator-{uuid.uuid4().hex[:8]}@test.example",
        name="Operator", role=UserRole.security_operator,
        hashed_password=hash_password("test"),
    )
    db.add(non_admin)
    await db.flush()
    token = create_access_token(str(non_admin.id))

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            # Create an enabled runbook
            rb_resp = await c.post("/api/runbooks", json={
                "name": "Force Test Runbook",
                "auto_execute": True,
                "tags": [],
                "steps": [
                    {"step_number": 1, "name": "Step", "type": "human_checkpoint",
                     "prompt": "Approve?", "required_role": "admin",
                     "timeout_hours": 1, "on_timeout": "abort", "on_failure": "abort",
                     "parallel_steps": []}
                ],
            })
            assert rb_resp.status_code == 201
            rb_id = rb_resp.json()["id"]

            # Non-admin with force=True should get 403
            resp = await c.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}, "force": True})
            assert resp.status_code == 403
            assert "admin" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)
