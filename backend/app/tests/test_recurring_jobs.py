# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.models.recurring_job import RecurringJob, RecurringJobType
from app.schemas.recurring_job import RecurringJobCreate, RecurringJobRead, RecurringJobUpdate
import uuid
from datetime import datetime, timezone


def test_recurring_job_type_enum():
    assert RecurringJobType.backup == "backup"
    assert RecurringJobType.scheduled_restore == "scheduled_restore"
    assert RecurringJobType.scheduled_op == "scheduled_op"


def test_recurring_job_create_schema():
    data = RecurringJobCreate(
        name="Daily DB Backup",
        job_type=RecurringJobType.backup,
        action_id="ssm_command",
        parameters={"instance_id": "i-123", "commands": ["echo hi"]},
        target_description="nexplane postgres DB",
        cron_expression="0 2 * * *",
        schedule_preset="daily",
        schedule_hour=2,
    )
    assert data.name == "Daily DB Backup"
    assert data.job_type == RecurringJobType.backup
    assert data.connector_id is None


def test_recurring_job_update_schema_partial():
    update = RecurringJobUpdate(enabled=False)
    dumped = update.model_dump(exclude_none=True)
    assert dumped == {"enabled": False}


# ---------------------------------------------------------------------------
# recurring_job_service — cron helpers
# ---------------------------------------------------------------------------
from app.services.recurring_job_service import compute_next_run


def test_compute_next_run_returns_future():
    next_run = compute_next_run("0 2 * * *")
    assert next_run > datetime.now(tz=timezone.utc)


def test_compute_next_run_daily_2am():
    next_run = compute_next_run("0 2 * * *")
    assert next_run.hour == 2
    assert next_run.minute == 0


# ---------------------------------------------------------------------------
# API tests (require router registered in main.py — expected to fail until Task 6)
# ---------------------------------------------------------------------------
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_recurring_job(auth_client: AsyncClient):
    resp = await auth_client.post("/recurring-jobs", json={
        "name": "Daily DB Backup",
        "job_type": "backup",
        "action_id": "ssm_command",
        "parameters": {"instance_id": "i-123", "commands": ["echo hi"]},
        "target_description": "nexplane postgres DB",
        "cron_expression": "0 2 * * *",
        "schedule_preset": "daily",
        "schedule_hour": 2,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Daily DB Backup"
    assert data["job_type"] == "backup"
    assert data["enabled"] is True
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_list_recurring_jobs(auth_client: AsyncClient):
    await auth_client.post("/recurring-jobs", json={
        "name": "Test Job",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test target",
        "cron_expression": "0 * * * *",
    })
    resp = await auth_client.get("/recurring-jobs")
    assert resp.status_code == 200
    assert any(j["name"] == "Test Job" for j in resp.json())


@pytest.mark.asyncio
async def test_disable_recurring_job(auth_client: AsyncClient):
    create_resp = await auth_client.post("/recurring-jobs", json={
        "name": "Disable Me",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test",
        "cron_expression": "0 * * * *",
    })
    job_id = create_resp.json()["id"]
    resp = await auth_client.post(f"/recurring-jobs/{job_id}/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_delete_recurring_job(auth_client: AsyncClient):
    create_resp = await auth_client.post("/recurring-jobs", json={
        "name": "Delete Me",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test",
        "cron_expression": "0 * * * *",
    })
    job_id = create_resp.json()["id"]
    resp = await auth_client.delete(f"/recurring-jobs/{job_id}")
    assert resp.status_code == 204
    get_resp = await auth_client.get(f"/recurring-jobs/{job_id}")
    assert get_resp.status_code == 404
