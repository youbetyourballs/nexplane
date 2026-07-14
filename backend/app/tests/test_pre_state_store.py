# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.pre_state_store import PreStateStore


# Override the session-scoped autouse fixture from conftest.py so these
# pure-unit tests never attempt a real database connection.
@pytest.fixture(scope="session", autouse=True)
def create_tables():
    yield


def _mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_capture_inserts_row_with_correct_expiry():
    db = _mock_db()
    cr_id = uuid.uuid4()
    org_id = uuid.uuid4()
    state = {"old_value": "sg-12345", "resource_arn": "arn:aws:ec2:us-east-1::sg/sg-12345"}

    org_settings = MagicMock()
    org_settings.pre_state_retention_days = 30

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = org_settings
    db.execute.return_value = result_mock

    await PreStateStore.capture(db, cr_id, "step_0", org_id, state)

    db.add.assert_called_once()
    snapshot = db.add.call_args[0][0]
    assert snapshot.cr_id == cr_id
    assert snapshot.step_id == "step_0"
    assert snapshot.organization_id == org_id
    assert snapshot.state_json == state
    delta = snapshot.expires_at - snapshot.captured_at
    assert abs(delta.days - 30) <= 1


@pytest.mark.asyncio
async def test_retrieve_returns_state_when_not_expired():
    db = _mock_db()
    cr_id = uuid.uuid4()
    org_id = uuid.uuid4()
    expected_state = {"bucket_policy": '{"Version":"2012-10-17"}'}

    snapshot = MagicMock()
    snapshot.state_json = expected_state
    snapshot.expires_at = datetime.now(timezone.utc) + timedelta(days=10)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = snapshot
    db.execute.return_value = result_mock

    result = await PreStateStore.retrieve(db, cr_id, "step_0", org_id)
    assert result == expected_state


@pytest.mark.asyncio
async def test_retrieve_returns_none_when_not_found():
    db = _mock_db()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    result = await PreStateStore.retrieve(db, uuid.uuid4(), "step_0", uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_purge_expired_deletes_expired_rows():
    db = _mock_db()
    result_mock = MagicMock()
    result_mock.rowcount = 5
    db.execute.return_value = result_mock

    count = await PreStateStore.purge_expired(db)
    assert count == 5
    db.execute.assert_called_once()


@pytest.mark.asyncio
async def test_capture_uses_default_30_days_when_org_settings_missing():
    db = _mock_db()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None  # no org settings row
    db.execute.return_value = result_mock

    await PreStateStore.capture(db, uuid.uuid4(), "step_0", uuid.uuid4(), {"key": "val"})
    snapshot = db.add.call_args[0][0]
    delta = snapshot.expires_at - snapshot.captured_at
    assert abs(delta.days - 30) <= 1
