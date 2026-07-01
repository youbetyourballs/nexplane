# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import get_db
from app.main import app

import os
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://nexplane:nexplane_dev@db:5432/nexplane")


@pytest_asyncio.fixture(autouse=True)
async def override_db():
    """Override get_db with a per-test engine so asyncpg doesn't cross event loops."""
    _engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with _engine.connect() as conn:
        session = AsyncSession(bind=conn, expire_on_commit=False)
        async def _get_db_override():
            yield session
        app.dependency_overrides[get_db] = _get_db_override
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db, None)
            await session.close()
    await _engine.dispose()


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
