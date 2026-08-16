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
            return {"EngineVersion": "8.0.32", "DBInstanceStatus": "available"}
        if call_count[0] == 2:  # _get_snapshot
            return "rds:mydb-mysql-2026-08-16"
        if call_count[0] == 3:  # _modify
            return None
        # _poll — returns (status, version) tuple
        return ("available", "8.0.35")

    with patch("app.connectors.executors.aws.rds_mysql_upgrade._run", side_effect=fake_run):
        with patch("asyncio.sleep", new=AsyncMock()):
            from app.connectors.executors.aws.rds_mysql_upgrade import execute
            result = await execute(
                {"db_instance_identifier": "mydb-mysql", "target_version": "8.0.35"},
                [],
                connector,
            )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "8.0.32"
    assert result["current_version"] == "8.0.35"
    assert result["snapshot_id"] == "rds:mydb-mysql-2026-08-16"


async def test_already_at_version(connector):
    async def fake_run(fn):
        return {"EngineVersion": "8.0.35", "DBInstanceStatus": "available"}

    with patch("app.connectors.executors.aws.rds_mysql_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.aws.rds_mysql_upgrade import execute
        result = await execute(
            {"db_instance_identifier": "mydb-mysql", "target_version": "8.0.35"},
            [],
            connector,
        )
    assert result["status"] == "already_at_version"
    assert result["current_version"] == "8.0.35"


async def test_rollback_returns_partial_with_snapshot(connector):
    from app.connectors.executors.aws.rds_mysql_upgrade import rollback
    result = await rollback(
        {},
        {"db_instance_identifier": "mydb-mysql", "snapshot_id": "rds:mydb-mysql-2026-08-16", "previous_version": "8.0.32"},
        connector,
    )
    assert result["rolled_back"] is False
    assert "rds:mydb-mysql-2026-08-16" in result["reason"]
    assert result["snapshot_id"] == "rds:mydb-mysql-2026-08-16"
