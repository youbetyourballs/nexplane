# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: disk2vhd .vhdx capture over WinRM -> S3. Machine tier."""
import asyncio
import base64
import logging
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


def _ps_check(sess, script: str, label: str, retries: int = 3):
    # Prepend progress suppression to avoid CLIXML noise on first WinRM connection
    full_script = "$ProgressPreference = 'SilentlyContinue'; " + script
    import time as _time
    last_err = None
    for attempt in range(retries):
        try:
            r = sess.run_ps(full_script)
            if r.status_code != 0:
                err_msg = r.std_err.decode(errors='replace')
                if attempt < retries - 1:
                    logger.warning(
                        "disk2vhd: %s attempt %d/%d non-zero status: %s – retrying",
                        label, attempt + 1, retries, err_msg,
                    )
                    _time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"disk2vhd: {label} failed: {err_msg}")
            return r
        except RuntimeError:
            raise
        except Exception as exc:
            last_err = exc
            if attempt < retries - 1:
                logger.warning(
                    "disk2vhd: %s attempt %d/%d WinRM error: %s – retrying",
                    label, attempt + 1, retries, exc,
                )
                _time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"disk2vhd: {label} WinRM error: {exc}") from exc
    raise RuntimeError(f"disk2vhd: {label} failed after {retries} attempts: {last_err}")


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
    # Allow caller to override the output directory (e.g. D:\ when C: has insufficient
    # free space to hold the VHDx while also being the source drive).
    _vhdx_dir = params.get("_vhdx_output_dir", r"C:\Windows\Temp")
    remote_vhdx = rf"{_vhdx_dir}\{vhdx_name}"

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
        chunk = 1500  # WinRM EncodedCommand limit is large; 1500 b64 chars is safe
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

        # Run disk2vhd via Start-Process (persists across WinRM sessions) with a
        # sentinel file written on completion.  Polling reads the sentinel each time.
        drives_str = " ".join(disk_list)
        sentinel = r"C:\Windows\Temp\disk2vhd_done.txt"
        log_file = r"C:\Windows\Temp\disk2vhd_out.txt"
        # Clean any stale sentinel from a prior run.  Use a fresh session here since
        # the exe-upload loop may have exhausted the original session's WinRM connection.
        # Do NOT use _ps_check here — Remove-Item with -ErrorAction SilentlyContinue is
        # inherently safe (no-ops if files don't exist) and the first WinRM call on a
        # fresh session can return CLIXML noise that sets status_code=1 even on success.
        clean_init_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        clean_init_sess.run_ps(
            f"$ProgressPreference = 'SilentlyContinue'; "
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{sentinel}','{log_file}'"
        )
        # Start disk2vhd via a Windows Scheduled Task so it truly outlives the WinRM
        # session.  Start-Job / Start-Process jobs die when the WinRM agent session is
        # torn down because they remain inside its Windows Job Object.  A schtask runs
        # as SYSTEM in its own session and is not affected by WinRM lifecycle.
        wrapper_script = r"C:\Windows\Temp\disk2vhd_wrapper.ps1"
        task_name = "NexplaneDisk2vhd"
        wrapper_body = (
            f"$p = Start-Process -FilePath '{remote_exe}' "
            f"-ArgumentList '{drives_str}','{remote_vhdx}','/accepteula' "
            f"-RedirectStandardOutput '{log_file}' "
            f"-NoNewWindow -PassThru; "
            f"$p.WaitForExit(); "
            f"Set-Content -Path '{sentinel}' -Value $p.ExitCode"
        )
        # Use a fresh session to start the scheduled task.
        capture_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        # Write the wrapper script and register + run the scheduled task in one go.
        schtask_ps = (
            f"Set-Content -Path '{wrapper_script}' -Value \"{wrapper_body}\" -Encoding UTF8; "
            f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue; "
            f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
            f"  -Argument '-NonInteractive -WindowStyle Hidden -File \"{wrapper_script}\"'; "
            f"$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest; "
            f"$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
            f"$task = Register-ScheduledTask -TaskName '{task_name}' -Action $action "
            f"  -Principal $principal -Settings $settings -Force; "
            f"Start-ScheduledTask -TaskName '{task_name}'; "
            f"'SCHTASK_STARTED'"
        )
        wrap_r = capture_sess.run_ps(schtask_ps)
        if wrap_r.status_code != 0 or b"SCHTASK_STARTED" not in wrap_r.std_out:
            raise RuntimeError(
                f"disk2vhd: start capture schtask failed: "
                f"{wrap_r.std_err.decode(errors='replace')} | "
                f"stdout: {wrap_r.std_out.decode(errors='replace')}"
            )
        logger.info("disk2vhd: capture scheduled task '%s' started", task_name)

        # Poll for sentinel file; each poll is a fresh short-lived WinRM session.
        # Use a generous timeout: disk2vhd C: capture saturates disk I/O and can make
        # WinRM sluggish for 60-90s per response even when the instance is still alive.
        import time as _time
        _poll_timeout = params.get("_winrm_poll_timeout_s", 120)
        capture_timeout = params.get("_capture_timeout_s", 7200)
        poll_start = _time.monotonic()
        while True:
            _time.sleep(30)
            poll_sess = winrm.Session(
                winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
                operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
            )
            sent_r = poll_sess.run_ps(f"Test-Path '{sentinel}'")
            done = sent_r.std_out.decode().strip().lower() == "true"
            logger.info("disk2vhd: capture sentinel present=%s", done)
            if done:
                break
            if _time.monotonic() - poll_start > capture_timeout:
                raise RuntimeError(
                    f"disk2vhd: capture job timed out after {capture_timeout}s"
                )
        # Read exit code from sentinel — use same generous timeout as poll sessions.
        exit_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
            operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
        )
        ec_r = exit_sess.run_ps(f"Get-Content '{sentinel}'")
        exit_code_str = ec_r.std_out.decode().strip()
        logger.info("disk2vhd: capture exit code=%s", exit_code_str)
        if exit_code_str != "0" or not exit_sess.run_ps(
            f"Test-Path '{remote_vhdx}'"
        ).std_out.decode().strip().lower().startswith("true"):
            log_out = exit_sess.run_ps(
                f"Get-Content -ErrorAction SilentlyContinue '{log_file}' | Out-String"
            ).std_out.decode(errors="replace")
            raise RuntimeError(
                f"disk2vhd: capture failed (exit={exit_code_str}). output: {log_out}"
            )

        # Size of produced vhdx
        size_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
            operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
        )
        size_r = _ps_check(size_sess, f"(Get-Item '{remote_vhdx}').Length", "stat vhdx")
        size_bytes = int(size_r.std_out.decode().strip())

        # Upload vhdx to S3 via presigned PUT, also detached so WinRM does not time out.
        presigned = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": archive_key},
            ExpiresIn=7200,
        )
        # Upload vhdx via a Windows Scheduled Task (same reason as capture: Start-Job
        # dies when the WinRM session is torn down).
        up_sentinel = r"C:\Windows\Temp\disk2vhd_upload_done.txt"
        up_log = r"C:\Windows\Temp\disk2vhd_upload_out.txt"
        up_wrapper_script = r"C:\Windows\Temp\disk2vhd_upload_wrapper.ps1"
        up_task_name = "NexplaneDisk2vhdUpload"
        up_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        # Do NOT use _ps_check here — Remove-Item with -ErrorAction SilentlyContinue is
        # inherently safe (no-ops if files don't exist) and the first WinRM call on a
        # fresh session can return CLIXML noise that sets status_code=1 even on success.
        up_sess.run_ps(
            f"$ProgressPreference = 'SilentlyContinue'; "
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{up_sentinel}','{up_log}'"
        )
        up_wrapper_body = (
            f"try {{"
            f"  Invoke-WebRequest -Method PUT -Uri '{presigned}'"
            f"    -InFile '{remote_vhdx}'"
            f"    -ContentType 'application/octet-stream'"
            f"    -UseBasicParsing | Out-File '{up_log}';"
            f"  Set-Content -Path '{up_sentinel}' -Value 0"
            f"}} catch {{"
            f"  $_ | Out-File '{up_log}';"
            f"  Set-Content -Path '{up_sentinel}' -Value 1"
            f"}}"
        )
        up_schtask_ps = (
            f"Set-Content -Path '{up_wrapper_script}' -Value \"{up_wrapper_body}\" -Encoding UTF8; "
            f"Unregister-ScheduledTask -TaskName '{up_task_name}' -Confirm:$false -ErrorAction SilentlyContinue; "
            f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
            f"  -Argument '-NonInteractive -WindowStyle Hidden -File \"{up_wrapper_script}\"'; "
            f"$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest; "
            f"$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
            f"$task = Register-ScheduledTask -TaskName '{up_task_name}' -Action $action "
            f"  -Principal $principal -Settings $settings -Force; "
            f"Start-ScheduledTask -TaskName '{up_task_name}'; "
            f"'SCHTASK_STARTED'"
        )
        up_job_r = up_sess.run_ps(up_schtask_ps)
        if up_job_r.status_code != 0 or b"SCHTASK_STARTED" not in up_job_r.std_out:
            raise RuntimeError(
                f"disk2vhd: start upload schtask failed: "
                f"{up_job_r.std_err.decode(errors='replace')} | "
                f"stdout: {up_job_r.std_out.decode(errors='replace')}"
            )
        logger.info("disk2vhd: upload scheduled task '%s' started", up_task_name)

        upload_timeout = params.get("_upload_timeout_s", 3600)
        up_poll_start = _time.monotonic()
        while True:
            _time.sleep(30)
            up_poll_sess = winrm.Session(
                winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
                operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
            )
            up_sent_r = up_poll_sess.run_ps(f"Test-Path '{up_sentinel}'")
            up_done = up_sent_r.std_out.decode().strip().lower() == "true"
            logger.info("disk2vhd: upload sentinel present=%s", up_done)
            if up_done:
                break
            if _time.monotonic() - up_poll_start > upload_timeout:
                raise RuntimeError(
                    f"disk2vhd: upload job timed out after {upload_timeout}s"
                )
        up_ec_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
            operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
        )
        up_ec = up_ec_sess.run_ps(f"Get-Content '{up_sentinel}'").std_out.decode().strip()
        if up_ec != "0":
            up_out = up_ec_sess.run_ps(
                f"Get-Content -ErrorAction SilentlyContinue '{up_log}' | Out-String"
            ).std_out.decode(errors="replace")
            raise RuntimeError(f"disk2vhd: upload failed (exit={up_ec}): {up_out}")

        # Cleanup remote files and scheduled tasks
        clean_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        clean_sess.run_ps(
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{remote_vhdx}','{remote_exe}',"
            f"'{wrapper_script}','{up_wrapper_script}';"
            f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue;"
            f"Unregister-ScheduledTask -TaskName '{up_task_name}' -Confirm:$false -ErrorAction SilentlyContinue"
        )
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
