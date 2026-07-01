# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/tests/test_backup.py
import pytest
from app.models.backup_target import BackupTarget, BackupTargetStatus


def test_backup_target_status_enum():
    assert BackupTargetStatus.healthy == "healthy"
    assert BackupTargetStatus.overdue == "overdue"
    assert BackupTargetStatus.unprotected == "unprotected"


from app.schemas.backup import BackupTargetRead, BackupContextRead, RestoreCrCreate
import uuid
from datetime import datetime, timezone


def test_backup_target_read_schema():
    bt = BackupTargetRead(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=None,
        asset_id=None,
        target_description="nexplane postgres DB",
        expected_cadence_hours=24,
        last_successful_backup_cr_id=None,
        last_successful_at=None,
        status=BackupTargetStatus.unprotected,
        created_at=datetime.now(tz=timezone.utc),
    )
    assert bt.status == BackupTargetStatus.unprotected


def test_restore_cr_create_schema():
    r = RestoreCrCreate(
        source_cr_id=uuid.uuid4(),
        target_description="nexplane postgres DB",
        restore_type="full",
        notes="Restoring after failed migration",
    )
    assert r.restore_type == "full"


def test_backup_context_read_schema():
    ctx = BackupContextRead(
        has_backup=True,
        last_successful_at=datetime.now(tz=timezone.utc),
        artifact={"type": "s3_object", "key": "backup.sql.gz"},
        backup_cr_id=uuid.uuid4(),
        overdue=False,
    )
    assert ctx.has_backup is True


from app.services.backup_target_service import compute_status
from datetime import timedelta


def test_compute_status_unprotected_when_no_job():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=None,
        target_description="test",
        expected_cadence_hours=24,
        status=BackupTargetStatus.unprotected,
    )
    assert compute_status(bt) == BackupTargetStatus.unprotected


def test_compute_status_healthy_when_recent():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=datetime.now(tz=timezone.utc) - timedelta(hours=20),
        status=BackupTargetStatus.healthy,
    )
    assert compute_status(bt) == BackupTargetStatus.healthy


def test_compute_status_overdue_when_stale():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=datetime.now(tz=timezone.utc) - timedelta(hours=30),
        status=BackupTargetStatus.overdue,
    )
    assert compute_status(bt) == BackupTargetStatus.overdue


def test_compute_status_overdue_when_never_run():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=None,
        status=BackupTargetStatus.unprotected,
    )
    assert compute_status(bt) == BackupTargetStatus.overdue


# ── API tests ────────────────────────────────────────────────────────────────

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_backup_target(auth_client: AsyncClient):
    resp = await auth_client.post("/backup-targets", json={
        "target_description": "nexplane postgres DB",
        "expected_cadence_hours": 24,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "unprotected"
    assert data["target_description"] == "nexplane postgres DB"


@pytest.mark.asyncio
async def test_list_backup_targets(auth_client: AsyncClient):
    await auth_client.post("/backup-targets", json={
        "target_description": "test target",
        "expected_cadence_hours": 24,
    })
    resp = await auth_client.get("/backup-targets")
    assert resp.status_code == 200
    assert any(t["target_description"] == "test target" for t in resp.json())


@pytest.mark.asyncio
async def test_backup_history_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/backup-history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_restore_cr_invalid_source(auth_client: AsyncClient):
    resp = await auth_client.post("/restore-crs", json={
        "source_cr_id": str(uuid.uuid4()),
        "target_description": "test",
        "restore_type": "full",
        "notes": "test restore",
    })
    assert resp.status_code == 404
