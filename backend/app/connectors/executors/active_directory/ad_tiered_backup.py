"""
ad_tiered_backup — Microsoft Tier Model backup for AD environments.

Tier 0 (IMPLEMENTED): Domain Controllers, PKI/CA servers.
  - Enumerates all DCs via WinRM (Get-ADDomainController -Filter *)
  - Runs ad_forest_snapshot (IFM + GPO) on each DC
  - Optionally backs up CA database on each CA server (certutil -backupDB)
  - Writes a tier manifest to S3 linking all per-DC snapshot prefixes
  - dry_run=True returns enumerated targets without executing

Tier 1 (STUB): Member servers — requires AWS Backup vault/plan configured in
  customer environment. Set up via AD_MEMBER_SERVER_BACKUP smoke phase.

Tier 2 (STUB): Workstations — requires Tier 1 environment plus WinRM access
  to workstations, which is typically blocked in production environments.

S3 layout:
  {s3_prefix}/tier0-manifest.json   — list of DCs backed up + per-DC prefix
  {s3_prefix}/{dc_name}/manifest.json  — per-DC snapshot manifest (from ad_forest_snapshot)
  {s3_prefix}/{dc_name}/IFM.zip
  {s3_prefix}/{dc_name}/GPO-backup.zip
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PowerShell helpers
# ---------------------------------------------------------------------------

_PS_ENUM_DCS = r"""
$dcs = Get-ADDomainController -Filter * | Select-Object -ExpandProperty HostName
$dcs -join "`n"
Write-Output "DC_ENUM_DONE"
"""

_PS_ENUM_CA = r"""
$cas = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration" `
    -ErrorAction SilentlyContinue
if ($cas) {
    $cas.CAServerName
    Write-Output "CA_FOUND"
} else {
    Write-Output "CA_NOT_FOUND"
}
"""

_PS_BACKUP_CA = r"""
param([string]$BackupPath)
if (Test-Path $BackupPath) { Remove-Item $BackupPath -Recurse -Force }
New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
certutil -backupDB $BackupPath 2>&1
if ($LASTEXITCODE -eq 0) { Write-Output "CA_BACKUP_OK" } else { Write-Output "CA_BACKUP_FAILED" }
"""


def _run_ps(creds: dict, script: str, hostname: str | None = None) -> tuple[str, str, int]:
    from ._client import run_winrm_ps
    return run_winrm_ps(creds, script, hostname)


def _run_ps_params(creds: dict, script: str, params: dict,
                   hostname: str | None = None) -> tuple[str, str, int]:
    prefix = "\n".join(
        f"${k} = '{str(v).replace(chr(39), chr(39)*2)}'" for k, v in params.items()
    )
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
    return _run_ps(creds, prefix + "\n" + body, hostname)


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
# Tier 0 — Domain Controllers
# ---------------------------------------------------------------------------

def _enumerate_dcs(creds: dict) -> list[str]:
    """Return list of DC hostnames via Get-ADDomainController."""
    out, err, rc = _run_ps(creds, _PS_ENUM_DCS)
    if rc != 0 or "DC_ENUM_DONE" not in out:
        raise RuntimeError(f"DC enumeration failed (rc={rc}): {err or out}")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return [ln for ln in lines if ln != "DC_ENUM_DONE"]


def _snapshot_dc(creds: dict, dc_hostname: str, s3_bucket: str,
                 s3_prefix: str) -> dict:
    """Run ad_forest_snapshot logic against a single DC."""
    from .ad_forest_snapshot import _do_snapshot
    dc_prefix = f"{s3_prefix}/{dc_hostname}"
    snap = _do_snapshot({**creds, "winrm_hostname": dc_hostname}, s3_bucket, dc_prefix)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "format": "ifm",
        "snapshot_id": f"ad-snapshot-{ts}",
        "snapshot_timestamp": ts,
        "dc_hostname": dc_hostname,
        "artifacts": snap["artifacts"],
        "artifact_sizes_bytes": snap["artifact_sizes_bytes"],
        "ad_ds_downtime_seconds": 0,
    }
    s3 = _s3_client(creds)
    s3.put_object(
        Bucket=s3_bucket,
        Key=f"{dc_prefix}/manifest.json",
        Body=json.dumps(manifest).encode(),
    )
    return {
        "dc_hostname": dc_hostname,
        "s3_prefix": dc_prefix,
        "artifacts": snap["artifacts"],
        "snapshot_id": manifest["snapshot_id"],
    }


def _backup_ca(creds: dict, ca_hostname: str, s3_bucket: str,
               s3_prefix: str) -> dict:
    """Backup CA database via certutil on a CA server."""
    import base64
    backup_path = r"C:\Temp\NexplaneCABackup"
    out, err, rc = _run_ps_params(
        {**creds, "winrm_hostname": ca_hostname},
        _PS_BACKUP_CA,
        {"BackupPath": backup_path},
        ca_hostname,
    )
    if "CA_BACKUP_OK" not in out:
        logger.warning("CA backup on %s may have failed: %s", ca_hostname, out[:200])

    # Read backup files and upload to S3
    list_out, _, _ = _run_ps(
        {**creds, "winrm_hostname": ca_hostname},
        f'Get-ChildItem "{backup_path}" -Recurse -File | Select-Object -ExpandProperty FullName',
        ca_hostname,
    )
    files = [f.strip() for f in list_out.splitlines() if f.strip()]

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for remote_path in files:
            b64_out, _, frc = _run_ps(
                {**creds, "winrm_hostname": ca_hostname},
                f'$bytes = [System.IO.File]::ReadAllBytes("{remote_path}"); '
                f'[Convert]::ToBase64String($bytes)',
                ca_hostname,
            )
            if frc == 0 and b64_out.strip():
                arcname = remote_path.replace(backup_path, "").lstrip("\\")
                zf.writestr(arcname, base64.b64decode(b64_out.strip()))

    ca_key = f"{s3_prefix}/{ca_hostname}/CA-backup.zip"
    s3 = _s3_client(creds)
    s3.put_object(Bucket=s3_bucket, Key=ca_key, Body=buf.getvalue())
    return {"ca_hostname": ca_hostname, "s3_key": ca_key, "size_bytes": len(buf.getvalue())}


def _do_tier0(creds: dict, s3_bucket: str, s3_prefix: str,
              ca_servers: list[str], dry_run: bool) -> dict:
    dc_hostnames = _enumerate_dcs(creds)

    if dry_run:
        return {
            "status": "dry_run_complete",
            "tier": "0",
            "targets": {
                "domain_controllers": dc_hostnames,
                "ca_servers": ca_servers,
            },
            "message": "dry_run=True — no backups executed",
        }

    dc_results = []
    dc_errors = []
    for dc in dc_hostnames:
        try:
            result = _snapshot_dc(creds, dc, s3_bucket, s3_prefix)
            dc_results.append(result)
            logger.info("Tier 0: snapshotted DC %s → %s", dc, result["s3_prefix"])
        except Exception as exc:
            logger.error("Tier 0: DC %s snapshot failed: %s", dc, exc)
            dc_errors.append({"dc_hostname": dc, "error": str(exc)})

    ca_results = []
    for ca in ca_servers:
        try:
            result = _backup_ca(creds, ca, s3_bucket, s3_prefix)
            ca_results.append(result)
        except Exception as exc:
            logger.warning("Tier 0: CA %s backup failed (non-fatal): %s", ca, exc)
            ca_results.append({"ca_hostname": ca, "error": str(exc)})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tier_manifest = {
        "format": "ad_tiered_backup_v1",
        "tier": "0",
        "backup_timestamp": ts,
        "s3_prefix": s3_prefix,
        "domain_controllers": dc_results,
        "ca_servers": ca_results,
        "errors": dc_errors,
    }
    s3 = _s3_client(creds)
    manifest_key = f"{s3_prefix}/tier0-manifest.json"
    s3.put_object(Bucket=s3_bucket, Key=manifest_key, Body=json.dumps(tier_manifest).encode())

    total = len(dc_results) + len(dc_errors)
    status = "completed" if not dc_errors else ("partial" if dc_results else "failed")
    return {
        "status": status,
        "tier": "0",
        "s3_bucket": s3_bucket,
        "s3_prefix": s3_prefix,
        "manifest_s3_key": manifest_key,
        "dcs_backed_up": len(dc_results),
        "dcs_failed": len(dc_errors),
        "ca_servers_backed_up": len([r for r in ca_results if "s3_key" in r]),
        "dc_results": dc_results,
        "errors": dc_errors,
    }


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    tier = parameters.get("tier", "0")
    s3_bucket = parameters.get("s3_bucket") or creds.get("s3_bucket", "")
    dry_run = bool(parameters.get("dry_run", False))

    if tier not in ("0", "1", "2"):
        return {"status": "error", "message": f"Invalid tier '{tier}' — must be '0', '1', or '2'"}

    if tier in ("1", "2"):
        return {
            "status": "not_implemented",
            "tier": tier,
            "message": (
                f"Tier {tier} requires an AWS Backup vault and plan pre-configured in the "
                "customer environment. Set up test infrastructure using the "
                "AD_MEMBER_SERVER_BACKUP smoke phase, then implement Tier 1/2 here."
            ),
        }

    if not s3_bucket:
        return {"status": "error", "message": "s3_bucket is required"}

    has_winrm = all(creds.get(k) for k in ("winrm_hostname", "winrm_username", "winrm_password"))
    if not has_winrm:
        return {
            "status": "error",
            "message": "WinRM credentials (winrm_hostname, winrm_username, winrm_password) required",
        }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s3_prefix = parameters.get("s3_prefix") or f"ad-tiered-backup/tier0/{ts}"
    ca_servers = parameters.get("ca_servers") or []

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _do_tier0(creds, s3_bucket, s3_prefix, ca_servers, dry_run),
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete all S3 objects written under the tier0 backup prefix."""
    creds = getattr(connector, "credentials", {}) or {}
    bucket = execution_result.get("s3_bucket")
    prefix = execution_result.get("s3_prefix")

    if not bucket or not prefix:
        return {"rolled_back": False, "reason": "No S3 location recorded — nothing to delete"}

    try:
        s3 = _s3_client(creds)
        paginator = s3.get_paginator("list_objects_v2")
        deleted = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                deleted.extend(k["Key"] for k in keys)
        return {
            "rolled_back": True,
            "deleted_count": len(deleted),
            "s3_prefix": prefix,
            "bucket": bucket,
        }
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
