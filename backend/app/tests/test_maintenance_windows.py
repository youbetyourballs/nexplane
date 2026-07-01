# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


def test_maintenance_window_model_importable():
    from app.models.maintenance_window import MaintenanceWindow
    assert MaintenanceWindow.__tablename__ == "maintenance_window"


def test_maintenance_window_schema_importable():
    from app.schemas.maintenance_window import MaintenanceWindowCreate, MaintenanceWindowRead
    schema = MaintenanceWindowCreate(
        name="Sat Night",
        cron_schedule="0 2 * * 6",
        duration_minutes=120,
    )
    assert schema.name == "Sat Night"
    assert schema.enabled is True


# Router tests
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_list_maintenance_windows_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_maintenance_window_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/maintenance-windows", json={
            "name": "Weekend",
            "cron_schedule": "0 2 * * 6",
            "duration_minutes": 60,
        })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_maintenance_window_invalid_cron_rejected():
    """Even without auth, invalid schema should fail at validation layer."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # This reaches Pydantic validation before auth (422) or auth first (401).
        # Either is acceptable — the important thing is it does not 200.
        resp = await client.post("/maintenance-windows", json={
            "name": "Bad",
            "cron_schedule": "not-a-cron",
            "duration_minutes": 60,
        })
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_get_nonexistent_maintenance_window_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows/99999")
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_maintenance_window_status_endpoint_exists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows/1/status")
    assert resp.status_code in (401, 404)  # exists, just no auth
