"""
ad_forest_snapshot — capture ntds.dit, SYSVOL, and GPO state to S3.

Sequence:
  1. Connect via WinRM to target DC
  2. Stop AD DS (NTDS service) — brief auth disruption
  3. Create VSS shadow copy of C:\\ and copy ntds.dit out
  4. Restart AD DS
  5. Record AD DS downtime
  6. Optionally compress SYSVOL tree
  7. Export all GPOs via Backup-GPO -All
  8. Download artifacts from DC to executor host, upload to S3 via boto3
  9. Clean up temp files on DC and executor host

Rollback (ad_forest_snapshot_cleanup):
  Deletes the S3 objects written during the snapshot.
  AD DS is already restarted before rollback could be called — nothing to undo on the DC.
"""

import asyncio
import io
import os
import tempfile
import zipfile
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

_PS_STOP_NTDS = "Stop-Service NTDS -Force; 'stopped'"

_PS_VSS_SNAPSHOT = r"""
# Create VSS shadow copy
$vssClass = [wmiclass]"root\cimv2:Win32_ShadowCopy"
$result = $vssClass.Create("C:\", "ClientAccessible")
if ($result.ReturnValue -ne 0) { throw "VSS create failed: $($result.ReturnValue)" }
$shadowId = $result.ShadowID
$shadow = Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq $shadowId }
$shadowDevice = $shadow.DeviceObject

# Mount shadow via symlink
$linkPath = "C:\NexplaneSnapshot"
if (Test-Path $linkPath) { cmd /c rmdir /s /q $linkPath }
cmd /c mklink /d $linkPath "$shadowDevice\"

# Copy ntds.dit
$dest = "C:\Temp\nexplane_ntds_snapshot.dit"
if (!(Test-Path "C:\Temp")) { New-Item -ItemType Directory -Path "C:\Temp" | Out-Null }
Copy-Item "$linkPath\Windows\NTDS\ntds.dit" $dest -Force

# Cleanup symlink and shadow
cmd /c rmdir $linkPath
$shadow.Delete()

$dest
"""

_PS_START_NTDS = "Start-Service NTDS; 'started'"

_PS_SYSVOL_ZIP = r"""
param([string]$OutPath)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sysvolSrc = "C:\Windows\SYSVOL\domain"
[System.IO.Compression.ZipFile]::CreateFromDirectory($sysvolSrc, $OutPath, 'Optimal', $false)
$OutPath
"""

_PS_GPO_BACKUP = r"""
param([string]$OutDir)
if (!(Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
Backup-GPO -All -Path $OutDir
$files = Get-ChildItem $OutDir -Recurse -File | Measure-Object
"backed_up=$($files.Count)"
"""

_PS_READ_FILE_B64 = r"""
param([string]$Path)
$bytes = [System.IO.File]::ReadAllBytes($Path)
[Convert]::ToBase64String($bytes)
"""

_PS_CLEANUP = r"""
param([string]$NtdsPath, [string]$SysvolZip, [string]$GpoDir)
if ($NtdsPath -and (Test-Path $NtdsPath)) { Remove-Item $NtdsPath -Force }
if ($SysvolZip -and (Test-Path $SysvolZip)) { Remove-Item $SysvolZip -Force }
if ($GpoDir -and (Test-Path $GpoDir)) { Remove-Item $GpoDir -Recurse -Force }
'cleaned'
"""


# ---------------------------------------------------------------------------
# WinRM helpers (shared pattern with dc_integrity_check)
# ---------------------------------------------------------------------------

def _winrm_client(creds: dict):
    import winrm

    host = creds["winrm_hostname"]
    port = int(creds.get("winrm_port", 5985))
    use_ssl = str(creds.get("winrm_use_ssl", "false")).lower() == "true"
    scheme = "https" if use_ssl else "http"

    return winrm.Protocol(
        endpoint=f"{scheme}://{host}:{port}/wsman",
        transport="basic",
        username=creds["winrm_username"],
        password=creds["winrm_password"],
        server_cert_validation="ignore",
    )


def _run_ps(protocol, script: str, args: list[str] | None = None) -> tuple[str, str, int]:
    cmd_args = ["-NonInteractive", "-NoProfile", "-Command", script]
    if args:
        cmd_args = ["-NonInteractive", "-NoProfile", "-File", "-"] + args

    shell_id = protocol.open_shell()
    try:
        command_id = protocol.run_command(
            shell_id, "powershell", ["-NonInteractive", "-NoProfile", "-Command", script]
        )
        stdout, stderr, status = protocol.get_command_output(shell_id, command_id)
        protocol.cleanup_command(shell_id, command_id)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            status,
        )
    finally:
        protocol.close_shell(shell_id)


def _run_ps_with_param(protocol, script: str, param_name: str, param_value: str) -> tuple[str, str, int]:
    """Run a parameterized script by inlining the param value."""
    inline = f'$__p = "{param_value}"\n' + script.replace(f"${param_name}", "$__p")
    # Simpler: build a one-liner that sets the param then calls the script body
    full_script = f'${param_name} = @"\n{param_value}\n"@\n{script}'
    return _run_ps(protocol, full_script)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _s3_client(creds: dict):
    import boto3

    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("aws_region"):
        kwargs["region_name"] = creds["aws_region"]
    return boto3.client("s3", **kwargs)


def _upload_bytes(s3, bucket: str, key: str, data: bytes) -> int:
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return len(data)


# ---------------------------------------------------------------------------
# Main snapshot logic (blocking — run in executor)
# ---------------------------------------------------------------------------

def _do_snapshot(creds: dict, s3_bucket: str, s3_prefix: str, include_sysvol: bool) -> dict:
    import base64

    proto = _winrm_client(creds)
    s3 = _s3_client(creds)
    artifacts = []
    artifact_sizes = {}

    # 1. Stop AD DS
    t_stop = datetime.now(timezone.utc)
    out, err, rc = _run_ps(proto, _PS_STOP_NTDS)
    if rc != 0:
        raise RuntimeError(f"Failed to stop NTDS service: {err}")

    try:
        # 2. VSS snapshot + copy ntds.dit
        ntds_path_out, err, rc = _run_ps(proto, _PS_VSS_SNAPSHOT)
        if rc != 0 or not ntds_path_out.strip():
            raise RuntimeError(f"VSS snapshot failed: {err}")
        ntds_remote_path = ntds_path_out.strip()

    finally:
        # 3. Always restart AD DS — measure downtime
        _run_ps(proto, _PS_START_NTDS)

    t_start = datetime.now(timezone.utc)
    ad_ds_downtime = int((t_start - t_stop).total_seconds())

    # 4. Download ntds.dit via base64 over WinRM
    b64_script = f'$Path = "{ntds_remote_path}"\n' + _PS_READ_FILE_B64.replace("param([string]$Path)", "")
    ntds_b64, _, rc = _run_ps(proto, b64_script)
    if rc != 0:
        raise RuntimeError("Failed to download ntds.dit from DC")
    ntds_bytes = base64.b64decode(ntds_b64.strip())
    ntds_s3_key = f"{s3_prefix}/ntds.dit"
    artifact_sizes["ntds.dit"] = _upload_bytes(s3, s3_bucket, ntds_s3_key, ntds_bytes)
    artifacts.append("ntds.dit")

    # 5. SYSVOL zip (optional)
    if include_sysvol:
        sysvol_zip_path = "C:\\Temp\\nexplane_sysvol.zip"
        zip_script = f'$OutPath = "{sysvol_zip_path}"\n' + _PS_SYSVOL_ZIP.replace("param([string]$OutPath)", "")
        _run_ps(proto, zip_script)
        b64_script = f'$Path = "{sysvol_zip_path}"\n' + _PS_READ_FILE_B64.replace("param([string]$Path)", "")
        sysvol_b64, _, rc = _run_ps(proto, b64_script)
        if rc == 0 and sysvol_b64.strip():
            sysvol_bytes = base64.b64decode(sysvol_b64.strip())
            sysvol_s3_key = f"{s3_prefix}/SYSVOL.zip"
            artifact_sizes["SYSVOL.zip"] = _upload_bytes(s3, s3_bucket, sysvol_s3_key, sysvol_bytes)
            artifacts.append("SYSVOL.zip")
    else:
        sysvol_zip_path = ""

    # 6. GPO backup — list of files zipped in-memory
    gpo_dir = "C:\\Temp\\NexplaneGPOBackup"
    gpo_script = f'$OutDir = "{gpo_dir}"\n' + _PS_GPO_BACKUP.replace("param([string]$OutDir)", "")
    _run_ps(proto, gpo_script)
    # Enumerate GPO backup files and download individually, assemble zip in-memory
    list_script = f'Get-ChildItem "{gpo_dir}" -Recurse -File | Select-Object -ExpandProperty FullName'
    file_list_raw, _, _ = _run_ps(proto, list_script)
    gpo_files = [f.strip() for f in file_list_raw.splitlines() if f.strip()]
    if gpo_files:
        gpo_zip_buf = io.BytesIO()
        with zipfile.ZipFile(gpo_zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for remote_path in gpo_files:
                b64_s = f'$Path = "{remote_path}"\n' + _PS_READ_FILE_B64.replace("param([string]$Path)", "")
                fb64, _, frc = _run_ps(proto, b64_s)
                if frc == 0 and fb64.strip():
                    arcname = remote_path.replace(gpo_dir, "").lstrip("\\")
                    zf.writestr(arcname, base64.b64decode(fb64.strip()))
        gpo_s3_key = f"{s3_prefix}/GPO-backup.zip"
        gpo_data = gpo_zip_buf.getvalue()
        artifact_sizes["GPO-backup.zip"] = _upload_bytes(s3, s3_bucket, gpo_s3_key, gpo_data)
        artifacts.append("GPO-backup.zip")

    # 7. Cleanup temp files on DC
    cleanup_script = (
        f'$NtdsPath = "{ntds_remote_path}"; '
        f'$SysvolZip = "{sysvol_zip_path}"; '
        f'$GpoDir = "{gpo_dir}"\n'
        + _PS_CLEANUP.replace("param([string]$NtdsPath, [string]$SysvolZip, [string]$GpoDir)", "")
    )
    _run_ps(proto, cleanup_script)

    return {
        "artifacts": artifacts,
        "artifact_sizes_bytes": artifact_sizes,
        "ntds_size_bytes": artifact_sizes.get("ntds.dit", 0),
        "ad_ds_downtime_seconds": ad_ds_downtime,
    }


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}

    s3_bucket = parameters["s3_bucket"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s3_prefix = parameters.get("s3_prefix") or f"ad-snapshots/{ts}"
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname") or creds.get("server", "unknown")
    include_sysvol = parameters.get("include_sysvol", True)

    has_winrm = all(creds.get(k) for k in ("winrm_hostname", "winrm_username", "winrm_password"))
    if not has_winrm:
        raise ValueError(
            "WinRM credentials (winrm_hostname, winrm_username, winrm_password) are required for ad_forest_snapshot"
        )

    loop = asyncio.get_event_loop()
    snapshot_data = await loop.run_in_executor(
        None, lambda: _do_snapshot(creds, s3_bucket, s3_prefix, bool(include_sysvol))
    )

    snapshot_id = f"ad-snapshot-{ts}"
    return {
        "snapshot_id": snapshot_id,
        "snapshot_timestamp": ts,
        "dc_hostname": dc_hostname,
        "s3_bucket": s3_bucket,
        "s3_prefix": s3_prefix,
        "artifacts": snapshot_data["artifacts"],
        "artifact_sizes_bytes": snapshot_data["artifact_sizes_bytes"],
        "ntds_size_bytes": snapshot_data["ntds_size_bytes"],
        "ad_ds_downtime_seconds": snapshot_data["ad_ds_downtime_seconds"],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Rollback: delete the S3 objects that were written.
    AD DS is already running — nothing to undo on the DC.
    """
    creds = getattr(connector, "credentials", {}) or {}
    bucket = execution_result.get("s3_bucket")
    prefix = execution_result.get("s3_prefix")
    artifacts = execution_result.get("artifacts", [])

    if not bucket or not prefix or not artifacts:
        return {"rolled_back": False, "reason": "No snapshot artifacts recorded — nothing to delete"}

    import boto3

    s3 = _s3_client(creds)
    deleted = []
    for artifact in artifacts:
        key = f"{prefix}/{artifact}"
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            deleted.append(key)
        except Exception as exc:
            pass  # best-effort

    return {
        "rolled_back": True,
        "deleted_s3_keys": deleted,
        "bucket": bucket,
        "note": "AD DS service is already running — no DC-side rollback required",
    }
