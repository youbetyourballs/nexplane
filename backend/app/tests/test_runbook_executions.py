"""
Tests for execution detail, resume (human checkpoint), and abort endpoints.
"""
import pytest
from httpx import AsyncClient


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


@pytest.mark.asyncio
async def test_get_execution_wrong_org_fails(client: AsyncClient, other_org_client: AsyncClient):
    """Execution from org A must not be visible to org B."""
    _, exec_id = await _create_and_trigger(client)
    resp = await other_org_client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 404
