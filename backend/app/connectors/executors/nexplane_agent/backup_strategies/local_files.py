# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: rsync/tar of a local filesystem path via SSH. Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _ssh_connect(creds: dict):
    import paramiko
    import io
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": creds["host"],
        "username": creds.get("username", "ec2-user"),
        "port": int(creds.get("port", 22)),
        "timeout": 30,
    }
    if creds.get("private_key"):
        connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(
            io.StringIO(creds["private_key"])
        )
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_path = params.get("source_path", "")
    if not source_path:
        raise RuntimeError("local_files: source_path is required")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("host"):
        raise RuntimeError(
            "local_files: connector must have SSH credentials (host, username, private_key/password)"
        )

    storage_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )
    cfg = storage_config.get("config", {})
    storage_type = storage_config["storage_type"]
    prefix = cfg.get("prefix", "backups/")
    captured_at = datetime.now(timezone.utc).isoformat()
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    archive_key = f"{prefix}{asset_id}/{captured_at.replace(':', '-')}.tar.gz"

    def _sync_tar_and_upload():
        ssh = _ssh_connect(creds)
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name

            cmd = f"tar czf - --warning=no-file-changed {source_path}"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            exit_code = stdout.channel.recv_exit_status()
            # tar exits 1 on "file changed as we read it" — treat as success
            if exit_code not in (0, 1):
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"local_files: tar failed (exit={exit_code}): {err}")
            return tmp_path
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        tmp_path = await loop.run_in_executor(pool, _sync_tar_and_upload)

    try:
        backend = get_backend(storage_type)
        artifact_uri = await backend.put_file(archive_key, tmp_path, cfg)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    artifact_refs = {
        "capture_strategy": "local_files",
        "restore_strategy": "file_restore_to_path",
        "backup_tier": "data",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "source_path": source_path,
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
