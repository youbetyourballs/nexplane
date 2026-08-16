# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "k", "secret_access_key": "s", "region": "us-east-1"}
    return c


async def test_upgrade_calls_modify_and_polls(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:  # _describe current version
            return {"EngineVersion": "14.9", "DBInstanceStatus": "available"}
        if call_count[0] == 2:  # _get_snapshot
            return "rds:mydb-2026-08-16"
        if call_count[0] == 3:  # _modify
            return None
        # _poll — returns (status, version) tuple
        return ("available", "15.4")

    with patch("app.connectors.executors.aws.rds_postgres_upgrade._run", side_effect=fake_run):
        with patch("asyncio.sleep", new=AsyncMock()):
            from app.connectors.executors.aws.rds_postgres_upgrade import execute
            result = await execute(
                {"db_instance_identifier": "mydb", "target_version": "15.4"},
                [],
                connector,
            )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "14.9"
    assert result["current_version"] == "15.4"
    assert result["snapshot_id"] == "rds:mydb-2026-08-16"


async def test_already_at_version(connector):
    async def fake_run(fn):
        return {"EngineVersion": "15.4", "DBInstanceStatus": "available"}

    with patch("app.connectors.executors.aws.rds_postgres_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.aws.rds_postgres_upgrade import execute
        result = await execute(
            {"db_instance_identifier": "mydb", "target_version": "15.4"},
            [],
            connector,
        )
    assert result["status"] == "already_at_version"
    assert result["current_version"] == "15.4"


async def test_rollback_returns_partial_with_snapshot(connector):
    from app.connectors.executors.aws.rds_postgres_upgrade import rollback
    result = await rollback(
        {},
        {"db_instance_identifier": "mydb", "snapshot_id": "rds:mydb-2026-08-16", "previous_version": "14.9"},
        connector,
    )
    assert result["rolled_back"] is False
    assert "rds:mydb-2026-08-16" in result["reason"]
    assert result["snapshot_id"] == "rds:mydb-2026-08-16"
