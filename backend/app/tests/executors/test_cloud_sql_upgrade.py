# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch


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
            return {"databaseVersion": "POSTGRES_14", "state": "RUNNABLE"}  # _get
        if call_count[0] == 3:
            return {}  # _patch
        return {"databaseVersion": "POSTGRES_15", "state": "RUNNABLE"}  # _poll

    with patch("app.connectors.executors.gcp.cloud_sql_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.gcp.cloud_sql_upgrade import execute
        result = await execute(
            {"instance_name": "my-sql", "target_version": "POSTGRES_15"},
            [], connector,
        )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "POSTGRES_14"


async def test_rollback_returns_partial(connector):
    from app.connectors.executors.gcp.cloud_sql_upgrade import rollback
    result = await rollback({}, {"instance_name": "my-sql", "previous_version": "POSTGRES_14"}, connector)
    assert result["rolled_back"] is False
