# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: download tar from storage backend, extract to remote path via SSH. Data tier."""
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


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies.launch_ami import _load_source_artifact_refs
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("file_restore_to_path: source_backup_cr_id is required")

    target_path = params.get("target_path", "")
    if not target_path:
        raise RuntimeError("file_restore_to_path: target_path is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    artifact_uri = artifact_refs.get("artifact_uri", "")
    storage_type = artifact_refs.get("storage_type", "s3")
    cfg = artifact_refs.get("config", {})

    if not artifact_uri:
        raise RuntimeError(
            f"file_restore_to_path: no artifact_uri in source CR {source_backup_cr_id}"
        )

    creds = getattr(connector, "credentials", {}) or {}

    backend = get_backend(storage_type)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await backend.get_file(artifact_uri, tmp_path, cfg)

        def _sync_extract():
            ssh = _ssh_connect(creds)
            try:
                ssh.exec_command(f"mkdir -p {target_path}")[1].channel.recv_exit_status()
                with open(tmp_path, "rb") as f:
                    sftp = ssh.open_sftp()
                    remote_tmp = f"/tmp/nexplane-restore-{os.path.basename(tmp_path)}"
                    sftp.putfo(f, remote_tmp)
                    sftp.close()
                cmd = f"tar xzf {remote_tmp} -C {target_path} --strip-components=0 && rm {remote_tmp}"
                _, stdout, stderr = ssh.exec_command(cmd)
                exit_code = stdout.channel.recv_exit_status()
                if exit_code != 0:
                    err = stderr.read(2048).decode(errors="replace")
                    raise RuntimeError(
                        f"file_restore_to_path: tar extract failed (exit={exit_code}): {err}"
                    )
            finally:
                ssh.close()

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_extract)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return {
        "status": "completed",
        "restore_strategy": "file_restore_to_path",
        "artifact_uri": artifact_uri,
        "target_path": target_path,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    # File extraction into a directory is not automatically reversible.
    # The caller should remove or overwrite the restored files manually.
    return {
        "rolled_back": False,
        "reason": (
            "file_restore_to_path rollback is not automatic"
            " — remove restored files from target_path manually"
        ),
    }
