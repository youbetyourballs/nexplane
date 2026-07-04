# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: disk2vhd .vhdx capture over WinRM -> S3. Machine tier."""
import asyncio
import base64
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _s3_client_from_creds(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _ps_check(sess, script: str, label: str):
    # Prepend progress suppression to avoid CLIXML noise on first WinRM connection
    full_script = "$ProgressPreference = 'SilentlyContinue'; " + script
    r = sess.run_ps(full_script)
    if r.status_code != 0:
        raise RuntimeError(f"disk2vhd: {label} failed: {r.std_err.decode(errors='replace')}")
    return r


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

    import winrm

    winrm_host = params["winrm_host"]
    winrm_username = params["winrm_username"]
    winrm_password = params["winrm_password"]
    disk_list = params.get("disk_list", ["C:"])
    aws_connector_id = params.get("aws_connector_id", "")

    creds = await _load_aws_creds(aws_connector_id, connector)
    storage_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )
    storage_type = storage_config["storage_type"]
    cfg = storage_config["config"]
    bucket = cfg["bucket"]
    prefix = cfg.get("prefix", "backups/")
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    captured_at = datetime.now(timezone.utc).isoformat()
    ts = captured_at.replace(":", "-")
    vhdx_name = f"backup_{ts}.vhdx"
    archive_key = f"{prefix}{asset_id}/{vhdx_name}"
    disk2vhd_s3_key = params.get("_disk2vhd_s3_key", "tools/disk2vhd.exe")
    remote_exe = r"C:\Windows\Temp\disk2vhd.exe"
    remote_vhdx = rf"C:\Windows\Temp\{vhdx_name}"

    # Resolve the disk2vhd.exe bytes: prefer local override (tests), else fetch from S3.
    local_exe = params.get("_disk2vhd_local_path")

    def _sync_capture():
        s3 = _s3_client_from_creds(creds)
        # Get disk2vhd.exe bytes
        if local_exe:
            with open(local_exe, "rb") as f:
                exe_bytes = f.read()
        else:
            obj = s3.get_object(Bucket=bucket, Key=disk2vhd_s3_key)
            exe_bytes = obj["Body"].read()

        sess = winrm.Session(winrm_host, auth=(winrm_username, winrm_password), transport="ntlm")

        # Upload disk2vhd.exe via base64-chunked PowerShell writes
        sess.run_ps(f"$ProgressPreference = 'SilentlyContinue'; Remove-Item -Force -ErrorAction SilentlyContinue '{remote_exe}'")
        b64 = base64.b64encode(exe_bytes).decode()
        chunk = 3000
        first = True
        for i in range(0, len(b64), chunk):
            part = b64[i:i + chunk]
            if first:
                _ps_check(sess,
                          f"Set-Content -Path '{remote_exe}.b64' -Value '{part}' -NoNewline",
                          "write exe chunk")
                first = False
            else:
                _ps_check(sess,
                          f"Add-Content -Path '{remote_exe}.b64' -Value '{part}' -NoNewline",
                          "write exe chunk")
        _ps_check(
            sess,
            f"$b=[IO.File]::ReadAllText('{remote_exe}.b64');"
            f"[IO.File]::WriteAllBytes('{remote_exe}',[Convert]::FromBase64String($b));"
            f"Remove-Item -Force '{remote_exe}.b64'",
            "decode exe",
        )

        # Run disk2vhd (synchronous; run_ps blocks until it exits)
        drives_str = " ".join(disk_list)
        _ps_check(
            sess,
            f"& '{remote_exe}' {drives_str} '{remote_vhdx}' /accepteula",
            "run disk2vhd",
        )

        # Size of produced vhdx
        size_r = _ps_check(sess, f"(Get-Item '{remote_vhdx}').Length", "stat vhdx")
        size_bytes = int(size_r.std_out.decode().strip())

        # Stream vhdx directly to S3 via presigned PUT
        presigned = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": archive_key},
            ExpiresIn=3600,
        )
        _ps_check(
            sess,
            f"Invoke-RestMethod -Method PUT -Uri '{presigned}' -InFile '{remote_vhdx}' "
            f"-ContentType 'application/octet-stream'",
            "upload vhdx to S3",
        )

        # Cleanup remote files
        sess.run_ps(f"Remove-Item -Force -ErrorAction SilentlyContinue '{remote_vhdx}','{remote_exe}'")
        return size_bytes

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        size_bytes = await loop.run_in_executor(pool, _sync_capture)

    artifact_uri = f"s3://{bucket}/{archive_key}"
    artifact_refs = {
        "capture_strategy": "disk2vhd",
        "restore_strategy": "import_image",
        "backup_tier": "machine",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "vhdx_filename": vhdx_name,
        "disk_list": disk_list,
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
