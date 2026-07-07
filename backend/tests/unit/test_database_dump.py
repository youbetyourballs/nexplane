# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import os
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.backup_strategies import database_dump
    return database_dump


def _make_storage_config():
    return {"storage_type": "s3", "config": {"bucket": "b", "prefix": "backups/"}}


def _make_connector(host="10.0.0.1", user="ec2-user", key="---FAKE---"):
    c = MagicMock()
    c.credentials = {"hostname": host, "username": user, "private_key": key}
    return c


@pytest.mark.asyncio
async def test_mysql_dump_uses_mysqldump():
    mod = _mod()
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.read.side_effect = [b"data", b""]
    stdout.channel.recv_exit_status.return_value = 0
    ssh.exec_command.return_value = (None, stdout, MagicMock())

    backend = MagicMock()
    backend.upload = asyncio.coroutine(lambda *a, **kw: "s3://b/backups/db/mydb/x/ts.sql.gz") if False else None
    backend.upload = MagicMock(return_value=asyncio.coroutine(lambda: "s3://b/key")())

    import asyncio as _asyncio

    async def _fake_upload(local_path, key, cfg):
        return f"s3://b/{key}"

    backend.upload = _fake_upload

    with patch.object(mod, "_ssh_connect", return_value=ssh), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump._load_storage_config",
             return_value=_make_storage_config(),
         ), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump.get_backend",
             return_value=backend,
         ):
        result = await mod.backup(
            {
                "db_type": "mysql",
                "db_host": "127.0.0.1",
                "db_port": 3306,
                "database_name": "mydb",
                "db_user": "root",
                "db_password": "pass",
                "backup_storage_id": "store1",
            },
            ["asset-1"],
            _make_connector(),
        )

    cmd = ssh.exec_command.call_args[0][0]
    assert "mysqldump" in cmd
    assert "mydb" in cmd
    assert result["artifact_refs"]["dump_format"] == "sql.gz"
    assert result["artifact_refs"]["db_type"] == "mysql"
    assert result["artifact_refs"]["artifact_uri"].endswith(".sql.gz")


@pytest.mark.asyncio
async def test_mongodb_dump_uses_mongodump():
    mod = _mod()
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.read.side_effect = [b"data", b""]
    stdout.channel.recv_exit_status.return_value = 0
    ssh.exec_command.return_value = (None, stdout, MagicMock())

    async def _fake_upload(local_path, key, cfg):
        return f"s3://b/{key}"

    backend = MagicMock()
    backend.upload = _fake_upload

    with patch.object(mod, "_ssh_connect", return_value=ssh), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump._load_storage_config",
             return_value=_make_storage_config(),
         ), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump.get_backend",
             return_value=backend,
         ):
        result = await mod.backup(
            {
                "db_type": "mongodb",
                "db_host": "127.0.0.1",
                "db_port": 27017,
                "database_name": "smokedb",
                "db_user": "root",
                "db_password": "pass",
                "backup_storage_id": "store1",
            },
            ["asset-1"],
            _make_connector(),
        )

    cmd = ssh.exec_command.call_args[0][0]
    assert "mongodump" in cmd
    assert "smokedb" in cmd
    assert result["artifact_refs"]["dump_format"] == "archive.gz"
    assert result["artifact_refs"]["db_type"] == "mongodb"
    assert result["artifact_refs"]["artifact_uri"].endswith(".archive.gz")


@pytest.mark.asyncio
async def test_unsupported_db_type_raises():
    mod = _mod()
    with pytest.raises(RuntimeError, match="unsupported db_type"):
        await mod.backup(
            {"db_type": "oracle", "backup_storage_id": "x", "database_name": "db"},
            [],
            _make_connector(),
        )
