# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.restore_strategies import database_restore
    return database_restore


def _artifact_refs(db_type="postgres", dump_format="sql.gz"):
    return {
        "capture_strategy": "database_dump",
        "storage_type": "s3",
        "config": {"bucket": "b"},
        "artifact_uri": f"s3://b/backups/db/mydb/x/ts.{dump_format}",
        "db_type": db_type,
        "dump_format": dump_format,
        "database_name": "mydb",
    }


def _params(db_type="postgres"):
    return {
        "source_backup_cr_id": "cr-123",
        "target_db_host": "127.0.0.1",
        "target_db_port": 5432,
        "target_db_name": "mydb_copy",
        "target_db_user": "postgres",
        "target_db_password": "secret",
    }


def _make_connector():
    c = MagicMock()
    c.credentials = {
        "hostname": "10.0.0.1",
        "username": "ec2-user",
        "private_key": "---FAKE---",
    }
    return c


def _make_ssh(exit_code=0):
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.channel.recv_exit_status.return_value = exit_code
    stdout.read.return_value = b""
    stderr = MagicMock()
    stderr.read.return_value = b""
    ssh.exec_command.return_value = (None, stdout, stderr)
    sftp = MagicMock()
    ssh.open_sftp.return_value = sftp
    return ssh, sftp


@pytest.mark.asyncio
async def test_postgres_restore_runs_psql():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("postgres", "sql.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        result = await mod.restore(_params("postgres"), [], _make_connector())

    assert result["status"] == "completed"
    assert result["db_type"] == "postgres"
    assert result["target_db_name"] == "mydb_copy"
    # restore command and verify command both use psql
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("psql" in c for c in calls)
    assert any("SELECT 1" in c for c in calls)


@pytest.mark.asyncio
async def test_mysql_restore_runs_mysql():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("mysql", "sql.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        params = _params()
        params["target_db_port"] = 3306
        result = await mod.restore(params, [], _make_connector())

    assert result["db_type"] == "mysql"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("mysql" in c and "SELECT 1" not in c for c in calls)


@pytest.mark.asyncio
async def test_mongodb_restore_runs_mongorestore():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("mongodb", "archive.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        params = _params()
        params["target_db_port"] = 27017
        result = await mod.restore(params, [], _make_connector())

    assert result["db_type"] == "mongodb"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("mongorestore" in c for c in calls)


@pytest.mark.asyncio
async def test_rollback_requires_confirm_drop():
    mod = _mod()
    result = await mod.rollback(
        {"target_db_host": "h", "target_db_name": "db"},
        {},
        MagicMock(),
    )
    assert result["rolled_back"] is False
    assert "confirm_drop" in result["reason"]


@pytest.mark.asyncio
async def test_rollback_drops_postgres_db():
    mod = _mod()
    ssh, _ = _make_ssh()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        result = await mod.rollback(
            {
                "confirm_drop": True,
                "target_db_host": "h",
                "target_db_port": 5432,
                "target_db_name": "mydb_copy",
                "target_db_user": "postgres",
                "target_db_password": "s",
                "db_type": "postgres",
            },
            {"db_type": "postgres"},
            _make_connector(),
        )

    assert result["rolled_back"] is True
    assert result["dropped_database"] == "mydb_copy"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("DROP DATABASE" in c for c in calls)
