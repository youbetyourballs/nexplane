# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
ad_tiered_backup — Microsoft Tier Model backup for AD environments.

Tier 0 (IMPLEMENTED): Domain Controllers, PKI/CA servers.
  - Enumerates all DCs via WinRM (Get-ADDomainController -Filter *)
  - Runs ad_forest_snapshot (IFM + GPO) on each DC
  - Optionally backs up CA database on each CA server (certutil -backupDB)
  - Writes a tier manifest to S3 linking all per-DC snapshot prefixes
  - dry_run=True returns enumerated targets without executing

Tier 1 (IMPLEMENTED): Member servers — EC2 instances backed up via AWS Backup.
  - Parameters: backup_vault_name, ec2_instance_ids (list), iam_role_arn
  - Creates the vault if it doesn't exist
  - Starts a backup job per instance and polls until all complete
  - Returns recovery_point_arns for rollback (delete_recovery_point)

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
Get-ADDomainController -Filter * | ForEach-Object {
    "$($_.HostName)|$($_.IPv4Address)"
}
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


def _boto_client(creds: dict, service: str):
    import boto3
    kwargs: dict = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("aws_region"):
        kwargs["region_name"] = creds["aws_region"]
    return boto3.client(service, **kwargs)


def _s3_client(creds: dict):
    return _boto_client(creds, "s3")


# ---------------------------------------------------------------------------
# Tier 0 — Domain Controllers
# ---------------------------------------------------------------------------

def _enumerate_dcs(creds: dict) -> list[dict]:
    """Return list of DC dicts with 'hostname' (FQDN) and 'ip' (IPv4) via Get-ADDomainController."""
    out, err, rc = _run_ps(creds, _PS_ENUM_DCS)
    if rc != 0 or "DC_ENUM_DONE" not in out:
        raise RuntimeError(f"DC enumeration failed (rc={rc}): {err or out}")
    dcs = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln or ln == "DC_ENUM_DONE":
            continue
        if "|" in ln:
            hostname, ip = ln.split("|", 1)
            dcs.append({"hostname": hostname.strip(), "ip": ip.strip()})
        else:
            dcs.append({"hostname": ln, "ip": ln})
    return dcs


def _snapshot_dc(creds: dict, dc_hostname: str, dc_ip: str, s3_bucket: str,
                 s3_prefix: str) -> dict:
    """Run ad_forest_snapshot logic against a single DC.

    Uses dc_ip for WinRM connectivity (resolves across VPC without domain DNS)
    and dc_hostname (FQDN) for manifest identification only.
    """
    from .ad_forest_snapshot import _do_snapshot
    dc_prefix = f"{s3_prefix}/{dc_hostname}"
    # Use IP for WinRM — FQDN may not resolve from the platform container
    snap = _do_snapshot({**creds, "winrm_hostname": dc_ip}, s3_bucket, dc_prefix)

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
    dc_list = _enumerate_dcs(creds)

    if dry_run:
        return {
            "status": "dry_run_complete",
            "tier": "0",
            "targets": {
                "domain_controllers": [dc["hostname"] for dc in dc_list],
                "ca_servers": ca_servers,
            },
            "message": "dry_run=True — no backups executed",
        }

    dc_results = []
    dc_errors = []
    for dc in dc_list:
        try:
            result = _snapshot_dc(creds, dc["hostname"], dc["ip"], s3_bucket, s3_prefix)
            dc_results.append(result)
            logger.info("Tier 0: snapshotted DC %s → %s", dc["hostname"], result["s3_prefix"])
        except Exception as exc:
            logger.error("Tier 0: DC %s snapshot failed: %s", dc["hostname"], exc)
            dc_errors.append({"dc_hostname": dc["hostname"], "error": str(exc)})

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
# Tier 1 — Member servers via AWS Backup
# ---------------------------------------------------------------------------

_BACKUP_JOB_POLL_INTERVAL = 30   # seconds between polls
_BACKUP_JOB_TIMEOUT = 7200       # 2 hours max per job


def _ensure_backup_vault(backup_client, vault_name: str) -> None:
    """Create the AWS Backup vault if it doesn't exist.

    DescribeBackupVault may return AccessDeniedException for certain vaults (e.g. the
    managed Default vault) even when the caller has full Backup permissions. We therefore
    treat any non-AlreadyExists error from create_backup_vault as the source of truth
    rather than relying on describe succeeding.
    """
    try:
        backup_client.describe_backup_vault(BackupVaultName=vault_name)
        return  # vault exists and is readable
    except Exception:
        pass  # fall through and try creating

    try:
        backup_client.create_backup_vault(BackupVaultName=vault_name)
        logger.info("Tier 1: created backup vault %s", vault_name)
    except Exception as exc:
        if "AlreadyExists" in type(exc).__name__ or "AlreadyExists" in str(exc):
            logger.info("Tier 1: backup vault %s already exists", vault_name)
        else:
            raise


def _start_backup_job(backup_client, vault_name: str, instance_id: str,
                      iam_role_arn: str, region: str, account_id: str) -> str:
    """Start an AWS Backup job for an EC2 instance and return the job ID."""
    resource_arn = f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}"
    resp = backup_client.start_backup_job(
        BackupVaultName=vault_name,
        ResourceArn=resource_arn,
        IamRoleArn=iam_role_arn,
    )
    return resp["BackupJobId"]


def _wait_backup_job(backup_client, job_id: str, instance_id: str) -> dict:
    """Poll until the backup job completes. Returns the final job description."""
    import time as _time
    deadline = _time.time() + _BACKUP_JOB_TIMEOUT
    while _time.time() < deadline:
        desc = backup_client.describe_backup_job(BackupJobId=job_id)
        state = desc.get("State", "")
        if state == "COMPLETED":
            return desc
        if state in ("FAILED", "ABORTED", "EXPIRED"):
            raise RuntimeError(
                f"Backup job {job_id} for {instance_id} ended in state {state}: "
                f"{desc.get('StatusMessage', '')}"
            )
        _time.sleep(_BACKUP_JOB_POLL_INTERVAL)
    raise TimeoutError(f"Backup job {job_id} for {instance_id} did not complete within {_BACKUP_JOB_TIMEOUT}s")


def _do_tier1(creds: dict, vault_name: str, ec2_instance_ids: list[str],
               iam_role_arn: str, dry_run: bool) -> dict:
    if dry_run:
        return {
            "status": "dry_run_complete",
            "tier": "1",
            "targets": {"ec2_instances": ec2_instance_ids},
            "message": "dry_run=True — no backups executed",
        }

    if not ec2_instance_ids:
        return {"status": "error", "tier": "1", "message": "ec2_instance_ids is required for tier 1"}

    backup_client = _boto_client(creds, "backup")
    _ensure_backup_vault(backup_client, vault_name)

    # Resolve region and account ID for resource ARNs
    sts_client = _boto_client(creds, "sts")
    identity = sts_client.get_caller_identity()
    account_id = identity["Account"]
    region = creds.get("aws_region") or "us-east-1"

    # Start all jobs
    jobs: list[dict] = []
    for iid in ec2_instance_ids:
        try:
            job_id = _start_backup_job(backup_client, vault_name, iid, iam_role_arn, region, account_id)
            jobs.append({"instance_id": iid, "job_id": job_id})
            logger.info("Tier 1: started backup job %s for instance %s", job_id, iid)
        except Exception as exc:
            logger.error("Tier 1: failed to start job for %s: %s", iid, exc)
            jobs.append({"instance_id": iid, "job_id": None, "error": str(exc)})

    # Wait for all started jobs
    results = []
    errors = []
    for job in jobs:
        if not job.get("job_id"):
            errors.append(job)
            continue
        try:
            desc = _wait_backup_job(backup_client, job["job_id"], job["instance_id"])
            rp_arn = desc.get("RecoveryPointArn", "")
            results.append({
                "instance_id": job["instance_id"],
                "job_id": job["job_id"],
                "recovery_point_arn": rp_arn,
                "backup_size_bytes": desc.get("BackupSizeInBytes", 0),
            })
            logger.info("Tier 1: backup complete for %s → %s", job["instance_id"], rp_arn)
        except Exception as exc:
            logger.error("Tier 1: job %s for %s failed: %s", job["job_id"], job["instance_id"], exc)
            errors.append({"instance_id": job["instance_id"], "job_id": job["job_id"], "error": str(exc)})

    status = "completed" if not errors else ("partial" if results else "failed")
    return {
        "status": status,
        "tier": "1",
        "backup_vault_name": vault_name,
        "instances_backed_up": len(results),
        "instances_failed": len(errors),
        "recovery_point_arns": [r["recovery_point_arn"] for r in results],
        "results": results,
        "errors": errors,
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

    if tier == "2":
        return {
            "status": "not_implemented",
            "tier": "2",
            "message": (
                "Tier 2 (workstations) requires WinRM access to workstations, "
                "which is typically blocked in production environments."
            ),
        }

    loop = asyncio.get_event_loop()

    if tier == "1":
        vault_name = parameters.get("backup_vault_name") or "nexplane-member-server-backup"
        ec2_instance_ids = parameters.get("ec2_instance_ids") or []
        iam_role_arn = parameters.get("iam_role_arn") or creds.get("iam_role_arn", "")
        if not iam_role_arn:
            return {"status": "error", "message": "iam_role_arn is required for tier 1 (AWS Backup)"}
        return await loop.run_in_executor(
            None,
            lambda: _do_tier1(creds, vault_name, ec2_instance_ids, iam_role_arn, dry_run),
        )

    # Tier 0
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

    return await loop.run_in_executor(
        None,
        lambda: _do_tier0(creds, s3_bucket, s3_prefix, ca_servers, dry_run),
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Roll back a tiered backup.

    Tier 0: delete all S3 objects under the backup prefix.
    Tier 1: delete each AWS Backup recovery point recorded in execution_result.
    """
    creds = getattr(connector, "credentials", {}) or {}
    tier = execution_result.get("tier", "0")

    if tier == "1":
        rp_arns = execution_result.get("recovery_point_arns") or []
        vault_name = execution_result.get("backup_vault_name", "")
        if not rp_arns:
            return {"rolled_back": True, "reason": "No recovery points recorded — nothing to delete"}
        backup_client = _boto_client(creds, "backup")
        deleted = []
        errors = []
        for arn in rp_arns:
            try:
                backup_client.delete_recovery_point(
                    BackupVaultName=vault_name,
                    RecoveryPointArn=arn,
                )
                deleted.append(arn)
                logger.info("Tier 1 rollback: deleted recovery point %s", arn)
            except Exception as exc:
                logger.error("Tier 1 rollback: failed to delete %s: %s", arn, exc)
                errors.append({"arn": arn, "error": str(exc)})
        return {
            "rolled_back": not errors,
            "deleted_count": len(deleted),
            "errors": errors,
        }

    # Tier 0 — delete S3 objects
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
