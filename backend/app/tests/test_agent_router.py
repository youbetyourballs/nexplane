import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_register_rejects_invalid_secret():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent/register",
            json={
                "machine_id": "test-machine-id",
                "hostname": "test-host",
                "os_type": "linux",
                "ip_addresses": ["10.0.0.1"],
                "os_version": "Ubuntu 22.04",
                "agent_version": "0.1.0",
            },
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_missing_auth_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent/register",
            json={
                "machine_id": "test-machine-id",
                "hostname": "test-host",
                "os_type": "linux",
                "ip_addresses": [],
                "os_version": "Ubuntu 22.04",
                "agent_version": "0.1.0",
            },
        )
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_poll_rejects_invalid_secret():
    fake_agent_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/agent/jobs/next?agent_id={fake_agent_id}",
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_result_rejects_invalid_secret():
    fake_job_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/agent/jobs/{fake_job_id}/result",
            json={"status": "completed", "result": {}, "error": None},
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 401
