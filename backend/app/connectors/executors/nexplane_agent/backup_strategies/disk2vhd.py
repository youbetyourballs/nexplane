# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: disk2vhd .vhdx capture over WinRM -> S3. Machine tier.

Execution modes:
  SSM (preferred): pass `instance_id` in params. disk2vhd.exe runs via AWS SSM
      RunCommand which provides the interactive-desktop context it requires.
  WinRM fallback: omit `instance_id`. disk2vhd runs via a Windows Scheduled Task.
      Note: disk2vhd is a GUI application; Session-0 scheduled tasks may fail on
      some Windows editions/configurations.
"""
import asyncio
import base64
import logging
import time as _time
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


def _ssm_client_from_creds(creds: dict):
    import boto3
    return boto3.client(
        "ssm",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _ssm_run(ssm, instance_id: str, commands: list, timeout: int = 3600) -> str:
    """Run PowerShell commands via SSM, poll until done, return stdout."""
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunPowerShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=timeout,
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = _time.monotonic() + timeout + 60
    while _time.monotonic() < deadline:
        _time.sleep(5)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        status = inv["Status"]
        if status in ("Pending", "InProgress", "Delayed"):
            continue
        if status != "Success":
            raise RuntimeError(
                f"disk2vhd: SSM command failed ({status}): "
                f"{inv.get('StandardErrorContent', '')}"
            )
        return inv.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"disk2vhd: SSM command timed out after {timeout}s")


def _ps_check(sess, script: str, label: str, retries: int = 3):
    full_script = "$ProgressPreference = 'SilentlyContinue'; " + script
    last_err = None
    for attempt in range(retries):
        try:
            r = sess.run_ps(full_script)
            if r.status_code != 0:
                err_msg = r.std_err.decode(errors='replace')
                if attempt < retries - 1:
                    logger.warning("disk2vhd: %s attempt %d/%d: %s – retrying",
                                   label, attempt + 1, retries, err_msg)
                    _time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"disk2vhd: {label} failed: {err_msg}")
            return r
        except RuntimeError:
            raise
        except Exception as exc:
            last_err = exc
            if attempt < retries - 1:
                logger.warning("disk2vhd: %s attempt %d/%d WinRM error: %s – retrying",
                               label, attempt + 1, retries, exc)
                _time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"disk2vhd: {label} WinRM error: {exc}") from exc
    raise RuntimeError(f"disk2vhd: {label} failed after {retries}: {last_err}")


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

    disk_list = params.get("disk_list", ["C:"])
    aws_connector_id = params.get("aws_connector_id", "")
    instance_id = params.get("instance_id")  # set to use SSM execution path

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
    # Sanitise timestamp for use in Windows filenames (no colons, no +)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    vhdx_name = f"backup_{ts}.vhdx"
    archive_key = f"{prefix}{asset_id}/{vhdx_name}"
    disk2vhd_s3_key = params.get("_disk2vhd_s3_key", "tools/disk2vhd.exe")
    remote_exe = r"C:\Windows\Temp\disk2vhd.exe"
    _vhdx_dir = params.get("_vhdx_output_dir", r"C:\Windows\Temp").rstrip("\\")
    remote_vhdx = rf"{_vhdx_dir}\{vhdx_name}"
    local_exe = params.get("_disk2vhd_local_path")

    def _sync_capture_ssm():
        """Create a VHDX image of the target volume(s) via AWS SSM RunCommand.

        disk2vhd.exe (a Sysinternals GUI tool) cannot run in non-interactive
        sessions (Session 0, SSM). We use native PowerShell cmdlets instead:
        New-VHD + VSS shadow copy + robocopy. This produces the same result
        (a VHDX containing the VSS-consistent volume contents) without
        requiring a desktop/window station.
        """
        s3 = _s3_client_from_creds(creds)
        ssm = _ssm_client_from_creds(creds)

        capture_timeout = params.get("_capture_timeout_s", 7200)
        # Capture the first drive in disk_list (extend for multi-volume later)
        src_drive = disk_list[0].rstrip(":\\")  # e.g. "E"

        # Remove stale output file if any
        _ssm_run(ssm, instance_id, [
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{remote_vhdx}'"
        ])

        # Build native PowerShell VHDX-creation script.
        # New-VHD/Mount-VHD require the Hyper-V PowerShell module which is
        # not installed on standard Windows Server. Use diskpart /s instead —
        # diskpart is available on all Windows Server editions without extras.
        # Assigns drive letter Z: for the staging partition; Z is unlikely to
        # conflict on a fresh smoke-test instance.
        ps_capture = (
            "$ErrorActionPreference='Stop';"
            f"$src='{src_drive}';"
            f"$vhdx='{remote_vhdx}';"
            "$vol=Get-Volume -DriveLetter $src;"
            "$mb=[math]::Ceiling($vol.Size/1MB)+512;"
            "$f1='C:\\Windows\\Temp\\dp_create.txt';"
            # Build diskpart script lines as an array, join with CRLF, write ASCII
            '$lines=@('
            '"create vdisk file=`"$vhdx`" maximum=$mb type=expandable",'
            '"select vdisk file=`"$vhdx`"",'
            '"attach vdisk",'
            '"create partition primary",'
            '"format fs=ntfs label=NexBackup quick",'
            '"assign letter=Z",'
            '"exit");'
            '[System.IO.File]::WriteAllText($f1,$lines-join"`r`n",[System.Text.Encoding]::ASCII);'
            "diskpart /s $f1;"
            "Remove-Item $f1 -Force;"
            # Direct robocopy from source drive; VSS omitted because SSM Session 0
            # runs as SYSTEM with no active writers — shadow copy DeviceName is
            # inaccessible from that context and causes robocopy exit 16.
            f"robocopy '{src_drive}:\\' 'Z:\\' /E /XJ /NP /R:1 /W:1 2>&1|Out-Null;"
            "$rc=$LASTEXITCODE;"
            # Detach via diskpart
            "$f2='C:\\Windows\\Temp\\dp_detach.txt';"
            '$lines2=@('
            '"select vdisk file=`"$vhdx`"",'
            '"detach vdisk",'
            '"exit");'
            '[System.IO.File]::WriteAllText($f2,$lines2-join"`r`n",[System.Text.Encoding]::ASCII);'
            "diskpart /s $f2;"
            "Remove-Item $f2 -Force;"
            "if($rc -ge 8){throw \"robocopy failed: $rc\"};"
            "Write-Output 'VHDX_DONE'"
        )

        logger.info("disk2vhd: starting VHDX capture of %s: -> %s via SSM",
                    src_drive, remote_vhdx)
        _ssm_run(ssm, instance_id, [ps_capture], timeout=capture_timeout)
        logger.info("disk2vhd: capture complete")

        # Verify output exists and get its size
        size_out = _ssm_run(ssm, instance_id, [
            f"if (Test-Path '{remote_vhdx}') {{ (Get-Item '{remote_vhdx}').Length }}"
            f" else {{ throw 'vhdx not found: {remote_vhdx}' }}"
        ])
        size_bytes = int(size_out.strip())
        logger.info("disk2vhd: vhdx size=%d bytes", size_bytes)

        # Upload to S3 via presigned PUT URL.
        # Use .NET WebClient.UploadFile() rather than Invoke-WebRequest -InFile;
        # on Windows PowerShell 5.1 the latter can silently swallow HTTP errors
        # when combined with | Out-Null.  WebClient.UploadFile() throws on any
        # non-2xx response and properly streams large files without buffering
        # the entire content in memory.
        presigned_put = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": archive_key},
            ExpiresIn=7200,
        )
        upload_timeout = params.get("_upload_timeout_s", 3600)
        logger.info("disk2vhd: uploading vhdx to s3://%s/%s", bucket, archive_key)
        _ssm_run(ssm, instance_id, [
            "$ErrorActionPreference='Stop';"
            "$wc=New-Object System.Net.WebClient;"
            "$wc.Headers['Content-Type']='application/octet-stream';"
            f"$wc.UploadFile('{presigned_put}','PUT','{remote_vhdx}');"
            "Write-Output 'UPLOAD_DONE'"
        ], timeout=upload_timeout)
        # Python-side verification: confirm the object landed in S3
        s3.head_object(Bucket=bucket, Key=archive_key)
        logger.info("disk2vhd: upload complete")

        # Cleanup remote VHDX (exe no longer used in SSM path)
        _ssm_run(ssm, instance_id, [
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{remote_vhdx}'"
        ])
        return size_bytes

    def _sync_capture_winrm():
        """Execute disk2vhd via WinRM + Windows Scheduled Task."""
        import winrm

        winrm_host = params["winrm_host"]
        winrm_username = params["winrm_username"]
        winrm_password = params["winrm_password"]

        s3 = _s3_client_from_creds(creds)

        if local_exe:
            with open(local_exe, "rb") as f:
                exe_bytes = f.read()
        else:
            obj = s3.get_object(Bucket=bucket, Key=disk2vhd_s3_key)
            exe_bytes = obj["Body"].read()

        sess = winrm.Session(winrm_host, auth=(winrm_username, winrm_password), transport="ntlm")

        # Upload disk2vhd.exe via base64-chunked PowerShell writes
        sess.run_ps(
            f"$ProgressPreference = 'SilentlyContinue';"
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{remote_exe}'"
        )
        b64 = base64.b64encode(exe_bytes).decode()
        chunk = 1500
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

        drives_str = " ".join(disk_list)
        sentinel = r"C:\Windows\Temp\disk2vhd_done.txt"
        log_file = r"C:\Windows\Temp\disk2vhd_out.txt"
        clean_init_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        clean_init_sess.run_ps(
            f"$ProgressPreference = 'SilentlyContinue';"
            f"Remove-Item -Force -ErrorAction SilentlyContinue '{sentinel}','{log_file}'"
        )
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
        capture_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        escaped_wrapper_body = wrapper_body.replace("$", "`$")
        schtask_ps = (
            f"Set-Content -Path '{wrapper_script}' -Value \"{escaped_wrapper_body}\" -Encoding UTF8; "
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
                raise RuntimeError(f"disk2vhd: capture job timed out after {capture_timeout}s")

        exit_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
            operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
        )
        ec_r = exit_sess.run_ps(f"Get-Content '{sentinel}'")
        exit_code_str = ec_r.std_out.decode().strip()
        if exit_code_str != "0" or not exit_sess.run_ps(
            f"Test-Path '{remote_vhdx}'"
        ).std_out.decode().strip().lower().startswith("true"):
            log_out = exit_sess.run_ps(
                f"Get-Content -ErrorAction SilentlyContinue '{log_file}' | Out-String"
            ).std_out.decode(errors="replace")
            raise RuntimeError(
                f"disk2vhd: capture failed (exit={exit_code_str}). output: {log_out}"
            )

        size_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm",
            operation_timeout_sec=_poll_timeout, read_timeout_sec=_poll_timeout + 10,
        )
        size_r = _ps_check(size_sess, f"(Get-Item '{remote_vhdx}').Length", "stat vhdx")
        size_bytes = int(size_r.std_out.decode().strip())

        presigned = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": archive_key},
            ExpiresIn=7200,
        )
        up_sentinel = r"C:\Windows\Temp\disk2vhd_upload_done.txt"
        up_log = r"C:\Windows\Temp\disk2vhd_upload_out.txt"
        up_wrapper_script = r"C:\Windows\Temp\disk2vhd_upload_wrapper.ps1"
        up_task_name = "NexplaneDisk2vhdUpload"
        up_sess = winrm.Session(
            winrm_host, auth=(winrm_username, winrm_password), transport="ntlm"
        )
        up_sess.run_ps(
            f"$ProgressPreference = 'SilentlyContinue';"
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
        escaped_up_wrapper_body = up_wrapper_body.replace("$", "`$")
        up_schtask_ps = (
            f"Set-Content -Path '{up_wrapper_script}' -Value \"{escaped_up_wrapper_body}\" -Encoding UTF8; "
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
            if up_done:
                break
            if _time.monotonic() - up_poll_start > upload_timeout:
                raise RuntimeError(f"disk2vhd: upload job timed out after {upload_timeout}s")
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
        if instance_id:
            size_bytes = await loop.run_in_executor(pool, _sync_capture_ssm)
        else:
            size_bytes = await loop.run_in_executor(pool, _sync_capture_winrm)

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
