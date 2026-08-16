# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"service_account_key_json": "{}", "project_id": "test-proj"}
    return c


async def test_upgrade_returns_upgraded(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return "test-proj"  # _get_project
        if call_count[0] == 2:
            m = MagicMock()
            m.redis_version = "REDIS_6_X"
            m.state = MagicMock(name="READY")
            return m  # _get instance
        if call_count[0] == 3:
            return MagicMock()  # _upgrade
        m = MagicMock()
        m.redis_version = "REDIS_7_0"
        m.state.name = "READY"
        return m  # _poll

    with patch("app.connectors.executors.gcp.memorystore_redis_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.gcp.memorystore_redis_upgrade import execute
        result = await execute(
            {"instance_name": "my-redis", "location": "us-central1", "target_version": "REDIS_7_0"},
            [], connector,
        )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "REDIS_6_X"


async def test_rollback_returns_partial(connector):
    from app.connectors.executors.gcp.memorystore_redis_upgrade import rollback
    result = await rollback({}, {"instance_name": "my-redis", "previous_version": "REDIS_6_X"}, connector)
    assert result["rolled_back"] is False
