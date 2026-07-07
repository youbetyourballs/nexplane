# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: download dump from storage backend, restore to target DB via SSH. Data tier."""
import asyncio
import io
import logging
import os
import tempfile
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import paramiko

from app.connectors.executors.nexplane_agent.restore_strategies import _load_source_artifact_refs
from app.connectors.executors.nexplane_agent.storage_backends import get_backend

logger = logging.getLogger(__name__)

_NAME = "database_restore"


def _shell_quote(s: str) -> str:
    if not s:
        return "''"
    return "'" + str(s).replace("'", "'\\''") + "'"


def _ssh_connect(creds: dict):
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
            raise RuntimeError("database_restore: could not load private key — unsupported key type")
        connect_kwargs["pkey"] = pkey
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def _get_ssh_creds(params: dict, connector, asset_ids: list) -> dict:
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
            logger.debug("database_restore: could not load asset connector creds: %s", _exc)
    if not (creds.get("hostname") or creds.get("host")):
        raise RuntimeError(
            "database_restore: connector must have SSH credentials (hostname/host, username, private_key/password)"
        )
    return creds


async def restore(params: dict, asset_ids: list, connector) -> dict:
    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("database_restore: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    db_type = artifact_refs.get("db_type", "postgres")
    storage_type = artifact_refs.get("storage_type", "s3")
    cfg = artifact_refs.get("config", {})
    artifact_uri = artifact_refs.get("artifact_uri", "")
    dump_format = artifact_refs.get("dump_format", "sql.gz")

    if not artifact_uri:
        raise RuntimeError(f"database_restore: no artifact_uri in source CR {source_backup_cr_id}")

    target_db_host = params.get("target_db_host", "localhost")
    target_db_port = params.get("target_db_port", 5432)
    target_db_name = params.get("target_db_name", "")
    target_db_user = params.get("target_db_user", "")
    target_db_password = params.get("target_db_password", "")

    if not target_db_name:
        raise RuntimeError("database_restore: target_db_name is required")

    creds = await _get_ssh_creds(params, connector, asset_ids)

    suffix = f".{dump_format}"
    run_id = str(_uuid.uuid4())[:8]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as _f:
        local_tmp = _f.name
    remote_tmp = f"/tmp/nexplane-dbrestore-{run_id}{suffix}"

    backend = get_backend(storage_type)
    await backend.download(artifact_uri, local_tmp, cfg)

    def _sync_restore():
        ssh = _ssh_connect(creds)
        try:
            with open(local_tmp, "rb") as f:
                sftp = ssh.open_sftp()
                sftp.putfo(f, remote_tmp)
                sftp.close()

            if db_type == "postgres":
                # Ensure the target database exists before restoring into it.
                create_db_cmd = (
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} postgres "
                    f'-c "CREATE DATABASE {target_db_name}" 2>&1 || true'
                )
                _, _co, _ce = ssh.exec_command(create_db_cmd)
                _co.channel.recv_exit_status()  # wait, ignore failure (already exists is fine)

                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} {_shell_quote(target_db_name)}"
                )
                verify_cmd = (
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} {_shell_quote(target_db_name)} "
                    f'-c "SELECT 1"'
                )
            elif db_type == "mysql":
                # Ensure the target database exists before restoring into it.
                create_db_cmd = (
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} "
                    f"-e 'CREATE DATABASE IF NOT EXISTS `{target_db_name}`'"
                )
                _, _co, _ce = ssh.exec_command(create_db_cmd)
                _create_exit = _co.channel.recv_exit_status()
                if _create_exit != 0:
                    _cerr = _ce.read(1024).decode(errors="replace").strip()
                    raise RuntimeError(
                        f"database_restore: failed to create target database "
                        f"'{target_db_name}' (exit={_create_exit}): {_cerr}"
                    )

                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} {_shell_quote(target_db_name)}"
                )
                verify_cmd = (
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} {_shell_quote(target_db_name)} "
                    f'-e "SELECT 1"'
                )
            else:  # mongodb
                _mongo_auth_restore = (
                    f"--username {_shell_quote(target_db_user)} "
                    f"--password {_shell_quote(target_db_password)} "
                    f"--authenticationDatabase admin "
                    if target_db_user else ""
                )
                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"mongorestore --host {target_db_host} --port {target_db_port} "
                    f"{_mongo_auth_restore}"
                    f"--archive "
                    f"--db {_shell_quote(target_db_name)}"
                )
                _mongo_auth_verify = (
                    f"--username {_shell_quote(target_db_user)} --password {_shell_quote(target_db_password)} "
                    if target_db_user else ""
                )
                verify_cmd = (
                    f"mongosh --host {target_db_host} --port {target_db_port} "
                    f"{_mongo_auth_verify}"
                    f'--eval "db.runCommand({{ping:1}})"'
                )

            _, stdout, stderr = ssh.exec_command(restore_cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore: restore failed (exit={exit_code}): {err}")

            _, vstdout, vstderr = ssh.exec_command(verify_cmd)
            vexit = vstdout.channel.recv_exit_status()
            if vexit != 0:
                err = vstderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore: verify failed (exit={vexit}): {err}")

            ssh.exec_command(f"rm -f {remote_tmp}")
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync_restore)

    try:
        os.unlink(local_tmp)
    except Exception:
        pass

    return {
        "status": "completed",
        "restore_strategy": "database_restore",
        "db_type": db_type,
        "target_db_name": target_db_name,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    if not params.get("confirm_drop"):
        return {
            "rolled_back": False,
            "reason": "confirm_drop not set — set confirm_drop=true to drop the restored database",
        }

    db_type = params.get("db_type") or execution_result.get("db_type", "postgres")
    target_db_host = params.get("target_db_host", "localhost")
    target_db_port = params.get("target_db_port", 5432)
    target_db_name = params.get("target_db_name", "")
    target_db_user = params.get("target_db_user", "")
    target_db_password = params.get("target_db_password", "")

    if not target_db_name:
        return {"rolled_back": False, "reason": "target_db_name is required for rollback"}

    creds = await _get_ssh_creds(params, connector, [])

    def _sync_drop():
        ssh = _ssh_connect(creds)
        try:
            if db_type == "postgres":
                drop_cmd = (
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} "
                    f'-c "DROP DATABASE IF EXISTS \\"{target_db_name}\\""'
                )
            elif db_type == "mysql":
                drop_cmd = (
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} "
                    f"-e 'DROP DATABASE IF EXISTS `{target_db_name}`'"
                )
            else:  # mongodb
                drop_cmd = (
                    f"mongosh --host {target_db_host} --port {target_db_port} "
                    f"--username {_shell_quote(target_db_user)} --password {_shell_quote(target_db_password)} "
                    f"--eval \"db.getSiblingDB(\\\"{target_db_name}\\\").dropDatabase()\""
                )
            _, stdout, stderr = ssh.exec_command(drop_cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore rollback: drop failed (exit={exit_code}): {err}")
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync_drop)

    return {"rolled_back": True, "dropped_database": target_db_name}
