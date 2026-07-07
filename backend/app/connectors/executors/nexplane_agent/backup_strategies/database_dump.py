# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: database dump via SSH (Postgres supported). Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_NAME = "database_dump"

_SUPPORTED_DB_TYPES = ("postgres", "mysql", "mongodb")


def _ssh_connect(creds: dict):
    import paramiko
    import io

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": creds.get("hostname") or creds.get("host"),
        "username": creds.get("username", "ec2-user"),
        "port": int(creds.get("port", 22)),
        "timeout": 30,
    }
    if creds.get("private_key"):
        key_str = creds["private_key"]
        pkey = None
        for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                pkey = cls.from_private_key(io.StringIO(key_str))
                break
            except Exception:
                continue
        if pkey is None:
            raise RuntimeError("database_dump: could not load private key — unsupported key type")
        connect_kwargs["pkey"] = pkey
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    db_type = params.get("db_type", "postgres")
    if db_type not in _SUPPORTED_DB_TYPES:
        raise RuntimeError(
            f"database_dump: unsupported db_type '{db_type}'. Supported: {_SUPPORTED_DB_TYPES}"
        )

    # SSH credentials come from the connector; also accept inline ssh_creds in params
    # or fall back to the asset's own connector creds.
    creds = getattr(connector, "credentials", {}) or {}
    if not (creds.get("hostname") or creds.get("host")):
        inline = params.get("ssh_creds") or {}
        if inline:
            creds = dict(creds)
            creds.update(inline)
    if not (creds.get("hostname") or creds.get("host")):
        try:
            asset_id_str = str(asset_ids[0]) if asset_ids else ""
            if asset_id_str:
                from app.database import AsyncSessionLocal
                from app.models.asset import Asset
                from app.models.connector import Connector as _Connector
                from app.services.connector_service import _attach_credentials
                import uuid as _uuid
                async with AsyncSessionLocal() as db:
                    asset = await db.get(Asset, _uuid.UUID(asset_id_str))
                    if asset and asset.connector_id:
                        conn = await db.get(_Connector, asset.connector_id)
                        if conn:
                            await _attach_credentials(conn, db)
                            _creds = conn.credentials or {}
                            if _creds.get("hostname") or _creds.get("host"):
                                creds = _creds
        except Exception as _exc:
            logger.debug("database_dump: could not load asset connector creds: %s", _exc)
    if not (creds.get("hostname") or creds.get("host")):
        raise RuntimeError(
            "database_dump: connector must have SSH credentials (hostname/host, username, private_key/password)"
        )

    # Database connection details: prefer explicit params, fall back to connector creds
    db_host = params.get("db_host") or creds.get("db_host", "localhost")
    db_port = params.get("db_port") or creds.get("db_port", 5432)
    db_name = params.get("database_name") or creds.get("dbname") or creds.get("database_name", "")
    db_user = params.get("db_user") or creds.get("db_user") or creds.get("user") or creds.get("username", "")
    db_password = params.get("db_password") or creds.get("db_password") or creds.get("password", "")

    if not db_name:
        raise RuntimeError("database_dump: database_name is required")

    storage_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )
    cfg = storage_config.get("config", {})
    storage_type = storage_config["storage_type"]
    prefix = cfg.get("prefix", "backups/")
    captured_at = datetime.now(timezone.utc).isoformat()
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    ts = captured_at.replace(":", "-")

    # key suffix determined by db_type (used before sync for temp naming, suffix corrected after)
    suffix = ".archive.gz" if db_type == "mongodb" else ".sql.gz"
    dump_key = f"{prefix}db/{db_name}/{asset_id}/{ts}{suffix}"

    def _sync_dump_and_upload():
        ssh = _ssh_connect(creds)
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name

            # Build dump command per db_type
            if db_type == "postgres":
                dump_cmd = (
                    f"PGPASSWORD={_shell_quote(db_password)} "
                    f"pg_dump -h {db_host} -p {db_port} -U {_shell_quote(db_user)} "
                    f"{_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            elif db_type == "mysql":
                dump_cmd = (
                    f"mysqldump -h {db_host} -P {db_port} -u {_shell_quote(db_user)} "
                    f"-p{_shell_quote(db_password)} {_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            else:  # mongodb
                dump_cmd = (
                    f"mongodump --host {db_host} --port {db_port} "
                    f"-u {_shell_quote(db_user)} -p {_shell_quote(db_password)} "
                    f"--authenticationDatabase admin --db {_shell_quote(db_name)} "
                    f"--archive | gzip"
                )
                dump_format = "archive.gz"

            _, stdout, stderr = ssh.exec_command(dump_cmd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(
                    f"database_dump: {db_type} dump failed (exit={exit_code}): {err}"
                )
            size_bytes = os.path.getsize(tmp_path)
            return tmp_path, size_bytes, dump_format
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        tmp_path, size_bytes, dump_format = await loop.run_in_executor(pool, _sync_dump_and_upload)

    try:
        backend = get_backend(storage_type)
        artifact_uri = await backend.upload(tmp_path, dump_key, cfg)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    artifact_refs = {
        "capture_strategy": "database_dump",
        "restore_strategy": "database_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "db_type": db_type,
        "dump_format": dump_format,
        "database_name": db_name,
        "size_bytes": size_bytes,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    artifact_uri = artifact_refs.get("artifact_uri", "")
    cfg = artifact_refs.get("config", {})

    if not artifact_uri:
        return {"rolled_back": False, "reason": "no artifact_uri in artifact_refs"}

    backend = get_backend(storage_type)
    await backend.delete(artifact_uri, cfg)
    return {"rolled_back": True, "deleted_artifact_uri": artifact_uri}


def _shell_quote(s: str) -> str:
    """Minimal shell quoting: wrap in single quotes, escape internal single quotes."""
    if not s:
        return "''"
    return "'" + str(s).replace("'", "'\\''") + "'"
