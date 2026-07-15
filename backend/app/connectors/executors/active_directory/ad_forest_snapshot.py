# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
ad_forest_snapshot — capture IFM media, GPO state, and manifest to S3.

IFM (Install From Media) is created by ntdsutil, which handles VSS internally.
No NTDS service stop is required — zero authentication downtime.

S3 layout:
  {prefix}/IFM.zip           — ntdsutil ifm output (ntds.dit + Registry/SYSTEM + SYSVOL)
  {prefix}/GPO-backup.zip    — individual GPO XML backups
  {prefix}/manifest.json     — format tag, timestamps, artifact inventory

Rollback: deletes the S3 objects written during the snapshot.
"""
import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

_PS_CREATE_IFM = r"""
param([string]$IFMDir)
if (Test-Path $IFMDir) { Remove-Item $IFMDir -Recurse -Force }
New-Item -ItemType Directory -Path $IFMDir -Force | Out-Null
$result = & ntdsutil "activate instance ntds" "ifm" "create full $IFMDir" "quit" "quit" 2>&1
if ($LASTEXITCODE -ne 0) { throw "ntdsutil ifm failed: $result" }
Write-Output "IFM_CREATED"
"""

_PS_ZIP_AND_UPLOAD = r"""
param([string]$IFMDir, [string]$ZipPath, [string]$PresignedUrl)
Compress-Archive -Path "$IFMDir\*" -DestinationPath $ZipPath -Force
$size = (Get-Item $ZipPath).Length
Invoke-WebRequest -Method PUT -Uri $PresignedUrl -InFile $ZipPath `
    -ContentType "application/octet-stream" -UseBasicParsing
Write-Output "IFM_UPLOADED:$size"
"""

_PS_GPO_BACKUP = r"""
param([string]$OutDir)
if (!(Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
Backup-GPO -All -Path $OutDir
$count = (Get-ChildItem $OutDir -Recurse -File | Measure-Object).Count
Write-Output "GPO_BACKED_UP:$count"
"""

_PS_READ_FILE_B64 = r"""
param([string]$Path)
$bytes = [System.IO.File]::ReadAllBytes($Path)
[Convert]::ToBase64String($bytes)
"""

_PS_CLEANUP = r"""
param([string]$IFMDir, [string]$ZipPath, [string]$GpoDir)
if ($IFMDir -and (Test-Path $IFMDir)) { Remove-Item $IFMDir -Recurse -Force }
if ($ZipPath -and (Test-Path $ZipPath)) { Remove-Item $ZipPath -Force }
if ($GpoDir -and (Test-Path $GpoDir)) { Remove-Item $GpoDir -Recurse -Force }
Write-Output "CLEANED"
"""


# ---------------------------------------------------------------------------
# WinRM helper
# ---------------------------------------------------------------------------

def _run_ps(creds: dict, script: str, dc_hostname: str | None = None) -> tuple[str, str, int]:
    from ._client import run_winrm_ps
    return run_winrm_ps(creds, script, dc_hostname)


def _run_ps_params(creds: dict, script: str, params: dict,
                   dc_hostname: str | None = None) -> tuple[str, str, int]:
    """Inline param() declarations then call _run_ps."""
    prefix = "\n".join(f"${k} = '{str(v).replace(chr(39), chr(39) * 2)}'" for k, v in params.items())
    body = script.strip()
    if body.startswith("param("):
        depth = 0
        for i, ch in enumerate(body):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    body = body[i + 1:].strip()
                    break
    return _run_ps(creds, prefix + "\n" + body, dc_hostname)


# ---------------------------------------------------------------------------
# S3 helper
# ---------------------------------------------------------------------------

def _s3_client(creds: dict):
    import boto3
    kwargs: dict = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("aws_region"):
        kwargs["region_name"] = creds["aws_region"]
    return boto3.client("s3", **kwargs)


# ---------------------------------------------------------------------------
# Core snapshot logic (blocking)
# ---------------------------------------------------------------------------

def _do_snapshot(creds: dict, s3_bucket: str, s3_prefix: str) -> dict:
    import base64

    s3 = _s3_client(creds)
    dc_hostname = creds.get("winrm_hostname")

    ifm_dir = r"C:\Temp\NexplaneIFM"
    ifm_zip = r"C:\Temp\NexplaneIFM.zip"
    gpo_dir = r"C:\Temp\NexplaneGPOBackup"

    # Step 1: Create IFM (ntdsutil handles VSS internally — no NTDS stop needed)
    out, err, rc = _run_ps_params(creds, _PS_CREATE_IFM, {"IFMDir": ifm_dir}, dc_hostname)
    if rc != 0 or "IFM_CREATED" not in out:
        raise RuntimeError(f"ntdsutil ifm create failed: {err or out}")

    # Step 2: Zip IFM and upload to S3 via presigned PUT URL
    ifm_s3_key = f"{s3_prefix}/IFM.zip"
    presigned_put = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": s3_bucket, "Key": ifm_s3_key, "ContentType": "application/octet-stream"},
        ExpiresIn=3600,
    )
    out, err, rc = _run_ps_params(creds, _PS_ZIP_AND_UPLOAD,
                                   {"IFMDir": ifm_dir, "ZipPath": ifm_zip,
                                    "PresignedUrl": presigned_put}, dc_hostname)
    if rc != 0 or "IFM_UPLOADED" not in out:
        raise RuntimeError(f"IFM zip/upload failed: {err or out}")
    ifm_size = 0
    for _line in out.splitlines():
        if _line.startswith("IFM_UPLOADED:"):
            try:
                ifm_size = int(_line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
            break

    # Step 3: GPO backup
    out, err, rc = _run_ps_params(creds, _PS_GPO_BACKUP, {"OutDir": gpo_dir}, dc_hostname)
    gpo_count = 0
    if rc == 0 and "GPO_BACKED_UP" in out:
        gpo_count = int(out.split("GPO_BACKED_UP:")[-1].strip().splitlines()[0])

    # Step 4: Download GPO backup via base64 and upload to S3 from executor
    gpo_s3_key = None
    gpo_size = 0
    if gpo_count > 0:
        list_out, _, _ = _run_ps(
            creds,
            f'Get-ChildItem "{gpo_dir}" -Recurse -File | Select-Object -ExpandProperty FullName',
            dc_hostname,
        )
        gpo_files = [f.strip() for f in list_out.splitlines() if f.strip()]
        if gpo_files:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for remote_path in gpo_files:
                    b64_script = f'$Path = "{remote_path}"\n' + _PS_READ_FILE_B64.replace(
                        "param([string]$Path)", ""
                    )
                    fb64, _, frc = _run_ps(creds, b64_script, dc_hostname)
                    if frc == 0 and fb64.strip():
                        arcname = remote_path.replace(gpo_dir, "").lstrip("\\")
                        zf.writestr(arcname, base64.b64decode(fb64.strip()))
            gpo_data = buf.getvalue()
            gpo_s3_key = f"{s3_prefix}/GPO-backup.zip"
            s3.put_object(Bucket=s3_bucket, Key=gpo_s3_key, Body=gpo_data)
            gpo_size = len(gpo_data)

    # Step 5: Cleanup temp files on DC
    _run_ps_params(creds, _PS_CLEANUP,
                   {"IFMDir": ifm_dir, "ZipPath": ifm_zip, "GpoDir": gpo_dir}, dc_hostname)

    artifacts = ["IFM.zip"]
    artifact_sizes: dict = {"IFM.zip": ifm_size}
    if gpo_s3_key:
        artifacts.append("GPO-backup.zip")
        artifact_sizes["GPO-backup.zip"] = gpo_size

    return {"artifacts": artifacts, "artifact_sizes_bytes": artifact_sizes}


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}

    has_winrm = all(creds.get(k) for k in ("winrm_hostname", "winrm_username", "winrm_password"))
    if not has_winrm:
        raise ValueError(
            "WinRM credentials (winrm_hostname, winrm_username, winrm_password) are required"
        )

    s3_bucket = parameters["s3_bucket"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s3_prefix = parameters.get("s3_prefix") or f"ad-snapshots/{ts}"
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname", "unknown")

    loop = asyncio.get_event_loop()
    snap = await loop.run_in_executor(
        None, lambda: _do_snapshot(creds, s3_bucket, s3_prefix)
    )

    # Write manifest
    snapshot_id = f"ad-snapshot-{ts}"
    manifest = {
        "format": "ifm",
        "snapshot_id": snapshot_id,
        "snapshot_timestamp": ts,
        "dc_hostname": dc_hostname,
        "domain_name": parameters.get("domain_name", creds.get("domain_name", "")),
        "artifacts": snap["artifacts"],
        "artifact_sizes_bytes": snap["artifact_sizes_bytes"],
        "ad_ds_downtime_seconds": 0,
    }
    s3 = _s3_client(creds)
    s3.put_object(
        Bucket=s3_bucket,
        Key=f"{s3_prefix}/manifest.json",
        Body=json.dumps(manifest).encode(),
    )

    return {
        "snapshot_id": snapshot_id,
        "snapshot_timestamp": ts,
        "dc_hostname": dc_hostname,
        "s3_bucket": s3_bucket,
        "s3_prefix": s3_prefix,
        "manifest_s3_key": f"{s3_prefix}/manifest.json",
        "artifacts": snap["artifacts"],
        "artifact_sizes_bytes": snap["artifact_sizes_bytes"],
        "ad_ds_downtime_seconds": 0,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    bucket = execution_result.get("s3_bucket")
    prefix = execution_result.get("s3_prefix")
    artifacts = execution_result.get("artifacts", [])

    if not bucket or not prefix or not artifacts:
        return {"rolled_back": False, "reason": "No snapshot artifacts recorded — nothing to delete"}

    s3 = _s3_client(creds)
    deleted = []
    for artifact in artifacts + ["manifest.json"]:
        key = f"{prefix}/{artifact}"
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            deleted.append(key)
        except Exception:
            pass

    return {
        "rolled_back": True,
        "deleted_s3_keys": deleted,
        "bucket": bucket,
        "note": "AD DS was never stopped — no DC-side rollback required",
    }
