# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: tar.gz of an NFS export mount point -> S3. Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _resolve_ssh_creds(params: dict, connector) -> dict:
    creds = dict(getattr(connector, "credentials", {}) or {})
    inline = params.get("ssh_creds") or {}
    if inline:
        creds.update(inline)
    if not (creds.get("hostname") or creds.get("host")):
        raise RuntimeError("nfs_files: SSH credentials required (host, username, private_key)")
    return creds


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config
    from app.connectors.executors.nexplane_agent.backup_strategies.local_files import _ssh_connect
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    nfs_export_path = params.get("nfs_export_path", "")
    if not nfs_export_path:
        raise RuntimeError("nfs_files: nfs_export_path is required")
    creds = _resolve_ssh_creds(params, connector)

    storage_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )
    storage_type = storage_config["storage_type"]
    cfg = storage_config["config"]
    prefix = cfg.get("prefix", "backups/")
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    captured_at = datetime.now(timezone.utc).isoformat()
    ts = captured_at.replace(":", "-")
    remote_tar = f"/tmp/nfs_files_{ts}.tar.gz"
    archive_key = f"{prefix}{asset_id}/{ts}.tar.gz"

    def _sync_capture():
        ssh = _ssh_connect(creds)
        local_tmp = None
        try:
            def _run(cmd, ok=(0,)):
                _, out, err = ssh.exec_command(cmd)
                data = out.read()
                code = out.channel.recv_exit_status()
                if code not in ok:
                    raise RuntimeError(
                        f"nfs_files: '{cmd}' exit={code}: {err.read(2048).decode(errors='replace')}"
                    )
                return data

            file_count = -1
            size_bytes = -1
            try:
                file_count = int(_run(f"find {nfs_export_path} -type f | wc -l").decode().strip())
            except Exception:
                pass
            try:
                size_bytes = int(_run(f"du -sb {nfs_export_path} | cut -f1").decode().strip())
            except Exception:
                pass

            # tar exits 1 on "file changed as we read it" — treat as success
            _run(f"sudo tar czf {remote_tar} {nfs_export_path}", ok=(0, 1))
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                local_tmp = tmp.name
            sftp = ssh.open_sftp()
            try:
                sftp.get(remote_tar, local_tmp)
            finally:
                sftp.close()
            _run(f"sudo rm -f {remote_tar}", ok=(0,))
            return local_tmp, file_count, size_bytes
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        local_tmp, file_count, size_bytes = await loop.run_in_executor(pool, _sync_capture)

    try:
        backend = get_backend(storage_type)
        artifact_uri = await backend.upload(local_tmp, archive_key, cfg)
    finally:
        try:
            os.unlink(local_tmp)
        except Exception:
            pass

    artifact_refs = {
        "capture_strategy": "nfs_files",
        "restore_strategy": "file_restore_to_path",
        "backup_tier": "data",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "nfs_export_path": nfs_export_path,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    refs = execution_result.get("artifact_refs", {})
    storage_type = refs.get("storage_type", "s3")
    artifact_uri = refs.get("artifact_uri", "")
    cfg = refs.get("config", {})
    if not artifact_uri:
        return {"rolled_back": False, "reason": "no artifact_uri in artifact_refs"}
    backend = get_backend(storage_type)
    await backend.delete(artifact_uri, cfg)
    return {"rolled_back": True, "deleted_artifact_uri": artifact_uri}
