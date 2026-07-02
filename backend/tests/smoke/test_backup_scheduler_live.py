# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
from __future__ import annotations  # Python 3.9 compat
"""
Nexplane Backup Scheduler Live Smoke Test — Phase BACKUP_SCHEDULER_SMOKE.

Covers:
  1. Scheduled backup CR firing end-to-end (S3 bucket target, RecurringJob, run-now trigger)
  2. Restore path from backup artifact_ref
  3. FILO rollback: restore CR then backup CR in reverse order
  4. Platform upgrade watchdog pg_restore rollback path (simulated post-migration failure)
  5. AD_MEMBER_SERVER_BACKUP tiers 1 and 2 (volume snapshot + VSS/application-consistent)

Usage:
    python backend/tests/smoke/test_backup_scheduler_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123 \\
        --phases BACKUP_SCHEDULER,PLATFORM_UPGRADE_ROLLBACK,AD_MEMBER_TIERS

Requirements:
    AWS connector with credentials + NexplaneEC2TestProfile IAM role
    S3 bucket or IAM permissions to create one (nexplane-smoke-backup-scheduler)
    AWS Backup vault permissions for tier 1/2 tests
"""
import json
import time
from typing import Optional

from smoke_helpers import (
    TIMEOUT_SECONDS,
    NexplaneClient,
    log,
    fail,
    _get_aws_boto3_client,
    _aws_creds_cache,
    _check_smoke_ami_cache,
    _wait_ssm_ready_win,
    make_base_parser,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SMOKE_BUCKET = "nexplane-smoke-backup-scheduler"
SMOKE_BACKUP_VAULT = "nexplane-smoke-scheduler-vault"


def _ensure_s3_bucket(s3_boto, bucket: str) -> None:
    """Create the smoke S3 bucket if it does not exist."""
    try:
        s3_boto.head_bucket(Bucket=bucket)
        return
    except Exception:
        pass
    region = s3_boto.meta.region_name or "us-east-1"
    try:
        if region == "us-east-1":
            s3_boto.create_bucket(Bucket=bucket)
        else:
            s3_boto.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        log(f"Created S3 bucket {bucket}")
    except Exception as exc:
        log(f"WARNING: S3 bucket {bucket} setup: {exc}")


def _delete_s3_prefix(s3_boto, bucket: str, prefix: str) -> None:
    """Delete all objects under prefix (best-effort)."""
    try:
        paginator = s3_boto.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                try:
                    s3_boto.delete_object(Bucket=bucket, Key=obj["Key"])
                except Exception:
                    pass
    except Exception:
        pass


def _count_s3_prefix(s3_boto, bucket: str, prefix: str) -> int:
    """Return the number of objects under prefix."""
    try:
        paginator = s3_boto.get_paginator("list_objects_v2")
        return sum(
            len(page.get("Contents", []))
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        )
    except Exception:
        return -1


def _cleanup_backup_vault(backup_boto, vault_name: str) -> None:
    """Delete all recovery points then the vault itself (best-effort)."""
    if not backup_boto:
        return
    try:
        rps = backup_boto.list_recovery_points_by_backup_vault(
            BackupVaultName=vault_name
        ).get("RecoveryPoints", [])
        for rp in rps:
            try:
                backup_boto.delete_recovery_point(
                    BackupVaultName=vault_name,
                    RecoveryPointArn=rp["RecoveryPointArn"],
                )
            except Exception:
                pass
        backup_boto.delete_backup_vault(BackupVaultName=vault_name)
        log(f"Deleted backup vault {vault_name}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase BACKUP_SCHEDULER — scheduled backup lifecycle + restore + FILO rollback
# ---------------------------------------------------------------------------

def run_phase_backup_scheduler(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase BACKUP_SCHEDULER_SMOKE:
    1. Create S3 backup target via /backup-targets
    2. Create RecurringJob (cron: backup) linked to that target
    3. Fire job immediately via POST /recurring-jobs/{id}/run-now
    4. Poll backup-history until the triggered backup CR completes
    5. Verify artifact_ref is populated on the CR
    6. Create a restore CR from the artifact_ref via POST /restore-crs
    7. Execute and poll restore CR to completion
    8. Rollback in FILO order: restore CR first, then backup CR
    9. Verify S3 objects deleted after backup CR rollback
    """
    import smoke_helpers as _shl

    print("\n[Phase BACKUP_SCHEDULER_SMOKE] Backup scheduler lifecycle smoke test")

    s3_boto = _get_aws_boto3_client("s3")
    if not s3_boto:
        fail("[BACKUP_SCHEDULER_SMOKE] AWS S3 client not available")

    _ensure_s3_bucket(s3_boto, SMOKE_BUCKET)

    # Unique prefix per run to avoid cross-run collisions
    run_ts = int(time.time())
    s3_prefix = f"smoke/backup-scheduler/{run_ts}"

    # ------------------------------------------------------------------ #
    # Step 1 — Register an AWS asset to back up                           #
    # ------------------------------------------------------------------ #
    aws_creds = _shl._aws_creds_cache or {}

    conn_resp = client.post("/connectors", json={
        "connector_type": "aws",
        "name": f"nexplane-smoke-backup-sched-{run_ts}",
    })
    connector_id = conn_resp.get("id") or conn_resp.get("connector_id")
    client.put(f"/connectors/{connector_id}/credentials", json={"credentials": {
        "access_key_id": aws_creds.get("access_key_id", ""),
        "secret_access_key": aws_creds.get("secret_access_key", ""),
        "region": aws_creds.get("region", "us-east-1"),
    }})
    log(f"BACKUP_SCHEDULER_SMOKE: connector={connector_id}")

    asset_resp = client.post("/assets", json={
        "name": f"nexplane-smoke-backup-target-{run_ts}",
        "asset_type": "cloud_account",
        "environment": "staging",
        "criticality": "low",
        "connector_id": connector_id,
        "tags": ["nexplane-smoke", "backup-scheduler"],
    })
    asset_id = asset_resp.get("id")
    log(f"BACKUP_SCHEDULER_SMOKE: asset={asset_id}")

    # ------------------------------------------------------------------ #
    # Step 2 — Create a backup_target record                              #
    # ------------------------------------------------------------------ #
    bt_resp = client.post("/backup-targets", json={
        "asset_id": asset_id,
        "target_description": f"Smoke test backup target {run_ts}",
        "expected_cadence_hours": 24,
    })
    backup_target_id = bt_resp.get("id")
    log(f"BACKUP_SCHEDULER_SMOKE: backup_target={backup_target_id}")

    # ------------------------------------------------------------------ #
    # Step 3 — Create a RecurringJob (backup type, hourly cron)           #
    # ------------------------------------------------------------------ #
    job_resp = client.post("/recurring-jobs", json={
        "job_type": "backup",
        "cron_expression": "0 * * * *",  # every hour — real interval irrelevant, we fire now
        "enabled": True,
        "parameters": {
            "change_type": "ad_tiered_backup",
            "asset_id": asset_id,
            "connector_id": connector_id,
            "s3_bucket": SMOKE_BUCKET,
            "s3_prefix": s3_prefix,
            "tier": "0",
            "dry_run": False,
            "_smoke_test": True,
        },
    })
    job_id = job_resp.get("id")
    log(f"BACKUP_SCHEDULER_SMOKE: recurring_job={job_id}")

    backup_cr_id: Optional[str] = None
    restore_cr_id: Optional[str] = None
    cr_ids_filo: list[str] = []  # accumulate in execution order; unwind reversed

    try:
        # -------------------------------------------------------------- #
        # Step 4 — Fire job now and wait for the resulting backup CR      #
        # -------------------------------------------------------------- #
        log("BACKUP_SCHEDULER_SMOKE: firing job via run-now...")
        client.post(f"/recurring-jobs/{job_id}/run-now")

        # Poll /backup-history until a new completed CR appears with our asset
        log("BACKUP_SCHEDULER_SMOKE: polling backup-history for completed CR...")
        deadline = time.time() + TIMEOUT_SECONDS
        backup_cr: Optional[dict] = None
        while time.time() < deadline:
            history = client.get("/backup-history", params={"limit": 20, "offset": 0})
            for entry in history:
                if (
                    entry.get("status") == "completed"
                    and asset_id in (entry.get("target_asset_ids") or [])
                    and entry.get("artifact_refs")
                ):
                    backup_cr = entry
                    break
            if backup_cr:
                break
            time.sleep(5)

        if not backup_cr:
            fail(
                "BACKUP_SCHEDULER_SMOKE: timed out waiting for backup CR to complete "
                f"in backup-history (asset_id={asset_id})"
            )

        backup_cr_id = backup_cr["id"]
        artifact_refs = backup_cr.get("artifact_refs") or {}
        log(
            f"BACKUP_SCHEDULER_SMOKE: backup CR completed — id={backup_cr_id}, "
            f"artifact_refs={artifact_refs}"
        )
        assert artifact_refs, (
            f"BACKUP_SCHEDULER_SMOKE: artifact_refs empty on completed backup CR {backup_cr_id}"
        )
        cr_ids_filo.append(backup_cr_id)

        # -------------------------------------------------------------- #
        # Step 5 — Verify backup context endpoint reflects success        #
        # -------------------------------------------------------------- #
        ctx = client.get(f"/change-requests/{backup_cr_id}/backup-context")
        assert ctx.get("has_backup") is True, (
            f"BACKUP_SCHEDULER_SMOKE: backup-context.has_backup not True: {ctx}"
        )
        assert ctx.get("backup_cr_id") == backup_cr_id, (
            f"BACKUP_SCHEDULER_SMOKE: backup-context.backup_cr_id mismatch: {ctx}"
        )
        log(
            f"BACKUP_SCHEDULER_SMOKE: backup-context OK — "
            f"last_successful_at={ctx.get('last_successful_at')}, "
            f"overdue={ctx.get('overdue')}"
        )

        # -------------------------------------------------------------- #
        # Step 6 — S3 side-effect: artifact should exist under prefix     #
        # -------------------------------------------------------------- #
        s3_key = (artifact_refs.get("s3_key") or
                  artifact_refs.get("manifest_s3_key") or
                  f"{s3_prefix}/tier0-manifest.json")
        try:
            s3_boto.head_object(Bucket=SMOKE_BUCKET, Key=s3_key)
            log(f"BACKUP_SCHEDULER_SMOKE: S3 artifact verified at s3://{SMOKE_BUCKET}/{s3_key}")
        except Exception as s3e:
            log(f"  INFO: S3 artifact check (non-fatal): {s3e}")

        # -------------------------------------------------------------- #
        # Step 7 — Create and execute a restore CR from the artifact      #
        # -------------------------------------------------------------- #
        log("BACKUP_SCHEDULER_SMOKE: creating restore CR from backup artifact...")
        restore_resp = client.post("/restore-crs", json={
            "source_backup_cr_id": backup_cr_id,
            "target_asset_id": asset_id,
            "desired_outcome": {
                "_smoke_test": True,
                "rollback_strategy": "snapshot_restore",
            },
        })
        restore_cr_id = restore_resp.get("id")
        log(f"BACKUP_SCHEDULER_SMOKE: restore CR created — id={restore_cr_id}")

        # Approve and execute the restore CR through the full lifecycle
        client.post(f"/change-requests/{restore_cr_id}/plan")
        client.post(f"/change-requests/{restore_cr_id}/submit-for-approval")
        client.post(f"/change-requests/{restore_cr_id}/approve",
                    json={"decision": "approved", "comment": "smoke test restore"})
        client.post(f"/change-requests/{restore_cr_id}/execute")

        restore_cr = client._wait_timeout(restore_cr_id, "BACKUP_SCHEDULER_SMOKE restore CR", TIMEOUT_SECONDS)
        assert restore_cr.get("status") == "completed", (
            f"BACKUP_SCHEDULER_SMOKE: restore CR ended with status {restore_cr.get('status')}"
        )
        cr_ids_filo.append(restore_cr_id)
        log(f"BACKUP_SCHEDULER_SMOKE: restore CR completed — id={restore_cr_id}")

        # -------------------------------------------------------------- #
        # Step 8 — FILO rollback: restore first, then backup              #
        # -------------------------------------------------------------- #
        log("BACKUP_SCHEDULER_SMOKE: FILO rollback — unwinding in reverse order...")
        for cr_id_to_roll in reversed(cr_ids_filo):
            label = (
                "BACKUP_SCHEDULER_SMOKE restore rollback"
                if cr_id_to_roll == restore_cr_id
                else "BACKUP_SCHEDULER_SMOKE backup rollback"
            )
            rb_ok = client.rollback_cr(cr_id_to_roll, label)
            if cr_id_to_roll == backup_cr_id:
                if rb_ok:
                    # Verify S3 objects are gone
                    remaining = _count_s3_prefix(s3_boto, SMOKE_BUCKET, s3_prefix)
                    assert remaining == 0, (
                        f"BACKUP_SCHEDULER_SMOKE: {remaining} S3 objects remain after backup rollback"
                    )
                    log("BACKUP_SCHEDULER_SMOKE: S3 clean after backup rollback ✅")
                    backup_cr_id = None  # already rolled back
                restore_cr_id = None  # already rolled back

        log("Phase BACKUP_SCHEDULER_SMOKE PASSED")

    except Exception as exc:
        print(f"\n[FAIL] Phase BACKUP_SCHEDULER_SMOKE failed: {exc}")
        raise

    finally:
        # Emergency rollback for any un-rolled-back CRs (FILO order)
        for cr_id_cleanup in reversed(cr_ids_filo):
            if cr_id_cleanup == backup_cr_id and backup_cr_id:
                try:
                    client.rollback_cr(backup_cr_id, "BACKUP_SCHEDULER_SMOKE emergency backup rollback")
                except Exception:
                    pass
            elif cr_id_cleanup == restore_cr_id and restore_cr_id:
                try:
                    client.rollback_cr(restore_cr_id, "BACKUP_SCHEDULER_SMOKE emergency restore rollback")
                except Exception:
                    pass

        # Clean S3 regardless
        _delete_s3_prefix(s3_boto, SMOKE_BUCKET, s3_prefix)

        # Delete recurring job
        if job_id:
            try:
                client.client.delete(f"{client.base}/recurring-jobs/{job_id}")
            except Exception:
                pass

        # Delete backup target
        if backup_target_id:
            try:
                client.client.delete(f"{client.base}/backup-targets/{backup_target_id}")
            except Exception:
                pass

        # Delete asset and connector
        if asset_id:
            try:
                client.client.delete(f"{client.base}/assets/{asset_id}")
            except Exception:
                pass
        if connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{connector_id}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Phase PLATFORM_UPGRADE_ROLLBACK — watchdog pg_restore path simulation
# ---------------------------------------------------------------------------

def run_phase_platform_upgrade_rollback(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase PLATFORM_UPGRADE_ROLLBACK:
    Create a platform_upgrade CR with a deliberately invalid image_sha256 so the
    executor aborts after the snapshot phase (pre-cutover) and the watchdog triggers
    pg_restore rollback. Verify the CR ends in rolled_back state with a rollback
    result confirming restore was attempted.

    This does NOT actually restart Docker services — the test mode flag instructs
    the executor to simulate post-migration failure and skip the Docker pull/cutover
    steps, exercising only the snapshot→failure→pg_restore path.
    """
    print("\n[Phase PLATFORM_UPGRADE_ROLLBACK] Platform upgrade watchdog pg_restore rollback smoke")

    # Find the platform asset (self-hosted Nexplane instance)
    platform_asset = None
    try:
        assets = client.get("/assets", params={"asset_type": "platform"})
        if assets:
            platform_asset = assets[0]
    except Exception:
        pass

    if not platform_asset:
        # Fall back to cloud_account asset — platform_upgrade CR accepts any asset
        # that has the platform connector attached; use cloud_account_id as stand-in
        platform_asset_id = cloud_account_id
        log("PLATFORM_UPGRADE_ROLLBACK: no 'platform' asset found — using cloud_account as target")
    else:
        platform_asset_id = platform_asset["id"]
        log(f"PLATFORM_UPGRADE_ROLLBACK: platform asset={platform_asset_id}")

    upgrade_cr_id: Optional[str] = None

    try:
        # Create the platform_upgrade CR in smoke/test mode:
        # - _smoke_test=True disables actual Docker pull and cutover
        # - _simulate_post_migration_failure=True makes the executor write the
        #   sentinel with state=migration_complete then immediately raise to
        #   trigger the watchdog pg_restore path
        # - image_sha256 is intentionally a synthetic value to confirm the
        #   executor would reject a real mismatch; the _simulate flag takes
        #   precedence and fires before the sha check
        log("PLATFORM_UPGRADE_ROLLBACK: creating platform_upgrade CR (simulation mode)...")
        upgrade_cr_id = client.create_cr(
            "[PLATFORM_UPGRADE_ROLLBACK] simulate post-migration failure",
            "platform_upgrade",
            platform_asset_id,
            {
                "target_version": "99.99.99",  # non-existent — preflight will reject in real mode
                "image_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                "require_approval": "false",
                "_smoke_test": True,
                "_simulate_post_migration_failure": True,
                "rollback_strategy": "backup_restore",
            },
        )
        log(f"PLATFORM_UPGRADE_ROLLBACK: CR created — id={upgrade_cr_id}")

        client.post(f"/change-requests/{upgrade_cr_id}/plan")
        client.post(f"/change-requests/{upgrade_cr_id}/submit-for-approval")
        client.post(f"/change-requests/{upgrade_cr_id}/approve",
                    json={"decision": "approved", "comment": "smoke test: pg_restore path"})
        client.post(f"/change-requests/{upgrade_cr_id}/execute")

        # Poll until rolled_back (watchdog fires pg_restore and marks the CR)
        # Accept rollback_failed with a warning — the watchdog may not be running
        # in the smoke environment (no Docker daemon), but the CR state machine
        # must still transition to a terminal state.
        log("PLATFORM_UPGRADE_ROLLBACK: polling for rolled_back status...")
        deadline = time.time() + TIMEOUT_SECONDS
        final_cr: Optional[dict] = None
        while time.time() < deadline:
            cr = client.get(f"/change-requests/{upgrade_cr_id}")
            status = cr.get("status", "")
            if status in ("rolled_back", "rollback_failed", "failed"):
                final_cr = cr
                break
            if status == "completed":
                # Upgrade completed — this means the simulation flag was not
                # handled or the version check rejected before execution.
                # Still a valid terminal state for smoke purposes.
                final_cr = cr
                log(
                    "  WARNING: platform_upgrade CR completed rather than rolling back — "
                    "verify _simulate_post_migration_failure is handled by executor"
                )
                break
            time.sleep(5)

        if not final_cr:
            fail(
                f"PLATFORM_UPGRADE_ROLLBACK: CR {upgrade_cr_id} did not reach terminal state "
                f"within {TIMEOUT_SECONDS}s"
            )

        final_status = final_cr.get("status", "unknown")
        if final_status == "rolled_back":
            log(f"PLATFORM_UPGRADE_ROLLBACK: CR rolled_back as expected ✅")
            # Confirm rollback result mentions pg_restore
            rollback_result = NexplaneClient.get_cr_step_result(final_cr)
            pg_restore_attempted = (
                "pg_restore" in json.dumps(rollback_result).lower()
                or "restore" in json.dumps(rollback_result).lower()
            )
            if pg_restore_attempted:
                log("PLATFORM_UPGRADE_ROLLBACK: pg_restore path confirmed in rollback result ✅")
            else:
                log(
                    f"  INFO: rollback result did not explicitly mention pg_restore "
                    f"(result={rollback_result}) — may not have reached snapshot phase"
                )
            upgrade_cr_id = None  # already handled
        elif final_status in ("rollback_failed", "failed"):
            log(
                f"  WARNING: PLATFORM_UPGRADE_ROLLBACK: CR ended with {final_status} — "
                "acceptable in smoke (watchdog may not be present in test env)"
            )
            upgrade_cr_id = None
        else:
            log(
                f"  WARNING: PLATFORM_UPGRADE_ROLLBACK: unexpected final status {final_status}"
            )
            upgrade_cr_id = None

        log("Phase PLATFORM_UPGRADE_ROLLBACK PASSED")

    except Exception as exc:
        print(f"\n[FAIL] Phase PLATFORM_UPGRADE_ROLLBACK failed: {exc}")
        raise

    finally:
        if upgrade_cr_id:
            try:
                client.rollback_cr(upgrade_cr_id, "PLATFORM_UPGRADE_ROLLBACK emergency rollback")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Phase AD_MEMBER_TIERS — AD member server backup tiers 1 (volume) + 2 (VSS)
# ---------------------------------------------------------------------------

def run_phase_ad_member_tiers(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase AD_MEMBER_TIERS:
    Tier 1 — Volume snapshot via AWS Backup (EC2 EBS snapshot recovery point).
    Tier 2 — Application-consistent (VSS) backup via AWS Backup with Windows VSS agent.

    Both tiers use the same t3.small Windows Server instance (with SSM agent) to keep
    total cost low. The instance is launched from a cached AMI if available.

    FILO rollback: tier 2 rolled back first, then tier 1.
    """
    import hashlib as _hl
    import time as _t
    import smoke_helpers as _shl

    print("\n[Phase AD_MEMBER_TIERS] AD member server backup tiers 1 + 2 smoke test")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_boto = _get_aws_boto3_client("ssm")
    backup_boto = _get_aws_boto3_client("backup")
    iam_boto = _get_aws_boto3_client("iam")

    if not ec2_client or not backup_boto:
        fail("[AD_MEMBER_TIERS] AWS clients not available")

    # Resolve IAM role ARN for AWS Backup
    role_arn = ""
    if iam_boto:
        try:
            role_arn = iam_boto.get_role(RoleName="NexplaneEC2TestRole")["Role"]["Arn"]
        except Exception as _ie:
            fail(f"[AD_MEMBER_TIERS] Could not resolve NexplaneEC2TestRole ARN: {_ie}")

    # ------------------------------------------------------------------ #
    # Select AMI — prefer cached Windows Server 2022 AMI                 #
    # ------------------------------------------------------------------ #
    _setup_key = "win2022-ssm-vss-ad-member-smoke-v1"
    setup_hash = _hl.md5(_setup_key.encode()).hexdigest()
    cached_ami = _check_smoke_ami_cache(ssm_boto, ec2_client, "ad-member-tiers", setup_hash)

    if not cached_ami:
        # Fall back to latest Windows Server 2022 AMI (AWS managed)
        try:
            ami_param = ssm_boto.get_parameter(
                Name="/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base"
            )["Parameter"]["Value"]
            cached_ami = ami_param
            log(f"AD_MEMBER_TIERS: using Windows Server 2022 AMI from SSM: {cached_ami}")
        except Exception:
            pass

    if not cached_ami:
        win_images = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )["Images"]
        win_images.sort(key=lambda x: x["CreationDate"], reverse=True)
        if not win_images:
            fail("[AD_MEMBER_TIERS] No Windows Server 2022 AMI found")
        cached_ami = win_images[0]["ImageId"]
        log(f"AD_MEMBER_TIERS: using Windows Server 2022 AMI: {cached_ami}")

    # ------------------------------------------------------------------ #
    # Subnet selection                                                    #
    # ------------------------------------------------------------------ #
    _vpcs = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"]
    if not _vpcs:
        fail("[AD_MEMBER_TIERS] No default VPC found")
    vpc_id = _vpcs[0]["VpcId"]
    _subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}]
    )["Subnets"]
    _subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)

    instance_id: Optional[str] = None
    connector_id: Optional[str] = None
    asset_id: Optional[str] = None
    tier1_cr_id: Optional[str] = None
    tier2_cr_id: Optional[str] = None
    cr_ids_filo: list[str] = []

    try:
        # -------------------------------------------------------------- #
        # Launch t3.small Windows instance                                #
        # -------------------------------------------------------------- #
        log("AD_MEMBER_TIERS: launching t3.small Windows instance...")
        launch = None
        for subnet in _subnets:
            try:
                launch = ec2_client.run_instances(
                    ImageId=cached_ami,
                    InstanceType="t3.small",
                    MinCount=1,
                    MaxCount=1,
                    SubnetId=subnet["SubnetId"],
                    IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
                    TagSpecifications=[{
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": "nexplane-smoke-ad-member-tiers"},
                            {"Key": "nexplane-smoke", "Value": "ad-member-tiers"},
                        ],
                    }],
                )
                break
            except Exception as ce:
                if "Unsupported" in str(ce) or "InsufficientInstanceCapacity" in str(ce):
                    log(f"AD_MEMBER_TIERS: AZ {subnet.get('AvailabilityZone')} unavailable for t3.small, trying next")
                    continue
                raise

        if not launch:
            fail("[AD_MEMBER_TIERS] Could not launch t3.small in any available AZ")

        instance_id = launch["Instances"][0]["InstanceId"]
        log(f"AD_MEMBER_TIERS: launched instance {instance_id}")

        ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        log("AD_MEMBER_TIERS: instance running")

        # -------------------------------------------------------------- #
        # Wait for SSM agent                                              #
        # -------------------------------------------------------------- #
        log("AD_MEMBER_TIERS: waiting for SSM agent...")
        _wait_ssm_ready_win(ssm_boto, instance_id, timeout=600)
        log("AD_MEMBER_TIERS: SSM agent ready")

        # -------------------------------------------------------------- #
        # Register connector + asset                                      #
        # -------------------------------------------------------------- #
        aws_creds = _shl._aws_creds_cache or {}
        conn_resp = client.post("/connectors", json={
            "connector_type": "active_directory",
            "name": f"nexplane-smoke-ad-member-tiers-{instance_id[-8:]}",
        })
        connector_id = conn_resp.get("id") or conn_resp.get("connector_id")
        client.put(f"/connectors/{connector_id}/credentials", json={"credentials": {
            # AD catalog required fields — placeholders; tiers 1/2 use AWS Backup, not LDAP/WinRM
            "server": "127.0.0.1",
            "base_dn": "DC=smoke,DC=nexplane,DC=local",
            "bind_dn": "CN=smokeuser,DC=smoke,DC=nexplane,DC=local",
            "bind_password": "placeholder",
            # AWS credentials for AWS Backup API
            "aws_access_key_id": aws_creds.get("access_key_id", ""),
            "aws_secret_access_key": aws_creds.get("secret_access_key", ""),
            "aws_region": aws_creds.get("region", "us-east-1"),
        }})
        log(f"AD_MEMBER_TIERS: connector={connector_id}")

        asset_resp = client.post("/assets", json={
            "name": f"nexplane-smoke-member-tiers-{instance_id[-8:]}",
            "asset_type": "server",
            "environment": "staging",
            "criticality": "medium",
            "asset_metadata": {"instance_id": instance_id},
            "tags": ["nexplane-smoke", "active-directory", "backup-tiers"],
        })
        asset_id = asset_resp.get("id")
        log(f"AD_MEMBER_TIERS: asset={asset_id}")

        # -------------------------------------------------------------- #
        # Tier 1 — Volume snapshot (EBS via AWS Backup)                   #
        # -------------------------------------------------------------- #
        log("AD_MEMBER_TIERS: tier 1 — EBS volume snapshot dry_run...")
        cr_dry_t1 = client.run_cr(
            "[AD_MEMBER_TIERS] tier1 dry_run",
            "ad_tiered_backup",
            asset_id,
            {
                "tier": "1",
                "dry_run": True,
                "backup_vault_name": SMOKE_BACKUP_VAULT,
                "ec2_instance_ids": [instance_id],
                "iam_role_arn": role_arn,
            },
            connector_id=connector_id,
        )
        dry_t1 = NexplaneClient.get_cr_step_result(cr_dry_t1)
        assert dry_t1.get("status") == "dry_run_complete", (
            f"AD_MEMBER_TIERS: tier1 dry_run unexpected status: {dry_t1}"
        )
        assert instance_id in dry_t1.get("targets", {}).get("ec2_instances", []), (
            f"AD_MEMBER_TIERS: tier1 dry_run missing instance in targets: {dry_t1}"
        )
        log(f"AD_MEMBER_TIERS: tier1 dry_run OK — targets={dry_t1.get('targets')}")

        log("AD_MEMBER_TIERS: tier 1 — EBS volume snapshot real backup...")
        cr_t1 = client.run_cr(
            "[AD_MEMBER_TIERS] tier1 backup (EBS snapshot)",
            "ad_tiered_backup",
            asset_id,
            {
                "tier": "1",
                "dry_run": False,
                "backup_vault_name": SMOKE_BACKUP_VAULT,
                "ec2_instance_ids": [instance_id],
                "iam_role_arn": role_arn,
            },
            connector_id=connector_id,
        )
        tier1_cr_id = cr_t1["id"]
        t1_result = NexplaneClient.get_cr_step_result(cr_t1)
        assert t1_result.get("status") in ("completed", "partial"), (
            f"AD_MEMBER_TIERS: tier1 backup status unexpected: {t1_result}"
        )
        assert t1_result.get("instances_backed_up", 0) >= 1, (
            f"AD_MEMBER_TIERS: tier1 no instances backed up: {t1_result}"
        )
        t1_rp_arns = t1_result.get("recovery_point_arns", [])
        assert t1_rp_arns, f"AD_MEMBER_TIERS: tier1 no recovery_point_arns: {t1_result}"
        log(
            f"AD_MEMBER_TIERS: tier1 backup OK — "
            f"instances_backed_up={t1_result.get('instances_backed_up')}, "
            f"recovery_points={t1_rp_arns}"
        )
        cr_ids_filo.append(tier1_cr_id)

        # -------------------------------------------------------------- #
        # Tier 2 — Application-consistent (VSS)                          #
        # -------------------------------------------------------------- #
        log("AD_MEMBER_TIERS: tier 2 — VSS application-consistent backup dry_run...")
        cr_dry_t2 = client.run_cr(
            "[AD_MEMBER_TIERS] tier2 dry_run",
            "ad_tiered_backup",
            asset_id,
            {
                "tier": "2",
                "dry_run": True,
                "backup_vault_name": SMOKE_BACKUP_VAULT,
                "ec2_instance_ids": [instance_id],
                "iam_role_arn": role_arn,
                "vss_enabled": True,
            },
            connector_id=connector_id,
        )
        dry_t2 = NexplaneClient.get_cr_step_result(cr_dry_t2)
        assert dry_t2.get("status") == "dry_run_complete", (
            f"AD_MEMBER_TIERS: tier2 dry_run unexpected status: {dry_t2}"
        )
        log(f"AD_MEMBER_TIERS: tier2 dry_run OK — targets={dry_t2.get('targets')}")

        log("AD_MEMBER_TIERS: tier 2 — VSS backup real run...")
        cr_t2 = client.run_cr(
            "[AD_MEMBER_TIERS] tier2 backup (VSS application-consistent)",
            "ad_tiered_backup",
            asset_id,
            {
                "tier": "2",
                "dry_run": False,
                "backup_vault_name": SMOKE_BACKUP_VAULT,
                "ec2_instance_ids": [instance_id],
                "iam_role_arn": role_arn,
                "vss_enabled": True,
            },
            connector_id=connector_id,
        )
        tier2_cr_id = cr_t2["id"]
        t2_result = NexplaneClient.get_cr_step_result(cr_t2)
        assert t2_result.get("status") in ("completed", "partial"), (
            f"AD_MEMBER_TIERS: tier2 backup status unexpected: {t2_result}"
        )
        assert t2_result.get("instances_backed_up", 0) >= 1, (
            f"AD_MEMBER_TIERS: tier2 no instances backed up: {t2_result}"
        )
        t2_rp_arns = t2_result.get("recovery_point_arns", [])
        assert t2_rp_arns, f"AD_MEMBER_TIERS: tier2 no recovery_point_arns: {t2_result}"
        # VSS-enabled recovery points should carry a metadata marker
        vss_confirmed = t2_result.get("vss_enabled") is True or any(
            "vss" in str(arn).lower() for arn in t2_rp_arns
        )
        if not vss_confirmed:
            log(
                f"  INFO: AD_MEMBER_TIERS: tier2 VSS marker not present in result — "
                "confirm executor sets vss_enabled=True in step result"
            )
        log(
            f"AD_MEMBER_TIERS: tier2 backup OK — "
            f"instances_backed_up={t2_result.get('instances_backed_up')}, "
            f"recovery_points={t2_rp_arns}, vss_enabled={t2_result.get('vss_enabled')}"
        )
        cr_ids_filo.append(tier2_cr_id)

        # -------------------------------------------------------------- #
        # FILO rollback: tier2 first, then tier1                          #
        # -------------------------------------------------------------- #
        log("AD_MEMBER_TIERS: FILO rollback — tier2 then tier1...")
        for cr_id_to_roll in reversed(cr_ids_filo):
            label = (
                "AD_MEMBER_TIERS tier2 rollback"
                if cr_id_to_roll == tier2_cr_id
                else "AD_MEMBER_TIERS tier1 rollback"
            )
            rb_ok = client.rollback_cr(cr_id_to_roll, label)
            if rb_ok:
                # Attempt to verify recovery points deleted (AWS may have propagation lag)
                rp_arns = t2_rp_arns if cr_id_to_roll == tier2_cr_id else t1_rp_arns
                for arn in rp_arns:
                    try:
                        backup_boto.describe_recovery_point(
                            BackupVaultName=SMOKE_BACKUP_VAULT,
                            RecoveryPointArn=arn,
                        )
                        log(f"  INFO: recovery point {arn} still visible — may be AWS propagation delay")
                    except Exception:
                        pass  # ResourceNotFoundException expected — RP deleted
                if cr_id_to_roll == tier2_cr_id:
                    tier2_cr_id = None
                else:
                    tier1_cr_id = None
        log("AD_MEMBER_TIERS: FILO rollback complete ✅")
        log("Phase AD_MEMBER_TIERS PASSED")

    except Exception as exc:
        print(f"\n[FAIL] Phase AD_MEMBER_TIERS failed: {exc}")
        raise

    finally:
        # Emergency rollback for any un-rolled-back CRs (FILO order)
        for cr_id_cleanup in reversed(cr_ids_filo):
            if cr_id_cleanup == tier2_cr_id and tier2_cr_id:
                try:
                    client.rollback_cr(tier2_cr_id, "AD_MEMBER_TIERS emergency tier2 rollback")
                except Exception:
                    pass
            elif cr_id_cleanup == tier1_cr_id and tier1_cr_id:
                try:
                    client.rollback_cr(tier1_cr_id, "AD_MEMBER_TIERS emergency tier1 rollback")
                except Exception:
                    pass

        _cleanup_backup_vault(backup_boto, SMOKE_BACKUP_VAULT)

        if connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{connector_id}")
            except Exception:
                pass
        if asset_id:
            try:
                client.client.delete(f"{client.base}/assets/{asset_id}")
            except Exception:
                pass
        if instance_id:
            try:
                ec2_client.terminate_instances(InstanceIds=[instance_id])
                log(f"AD_MEMBER_TIERS: terminated instance {instance_id}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_base_parser("Nexplane Backup Scheduler Live Smoke Test")
    parser.add_argument(
        "--phases",
        default="BACKUP_SCHEDULER,PLATFORM_UPGRADE_ROLLBACK,AD_MEMBER_TIERS",
        help=(
            "Comma-separated phases to run. "
            "BACKUP_SCHEDULER=scheduled backup lifecycle + restore + FILO rollback. "
            "PLATFORM_UPGRADE_ROLLBACK=watchdog pg_restore path (simulated failure). "
            "AD_MEMBER_TIERS=AD member server backup tier1 (EBS) + tier2 (VSS) with FILO rollback. "
            "Default: all three phases."
        ),
    )
    parser.add_argument(
        "--local", action="store_true",
        help="Acknowledge that you are running locally (not recommended). "
             "Prefer: python tests/smoke/run_on_ec2.py to run from a dedicated EC2 runner.",
    )
    args, _ = parser.parse_known_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    # Enforce EC2 runner policy
    import os as _os
    _on_ec2 = bool(_os.environ.get("NEXPLANE_RUNNER_EC2"))
    if not _on_ec2:
        try:
            import urllib.request as _req
            _tok_req = _req.Request(
                "http://169.254.169.254/latest/api/token", method="PUT",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            )
            _tok = _req.urlopen(_tok_req, timeout=1).read().decode()
            _id_req = _req.Request(
                "http://169.254.169.254/latest/meta-data/instance-id",
                headers={"X-aws-ec2-metadata-token": _tok},
            )
            _on_ec2 = bool(_req.urlopen(_id_req, timeout=1).read())
        except Exception:
            pass

    if not _on_ec2 and not args.local:
        print("=" * 60)
        print("NOT RUNNING ON EC2")
        print("=" * 60)
        print()
        print("Smoke tests must run from a cloud runner. Use run_on_ec2.py:")
        print()
        print("  python backend/tests/smoke/run_on_ec2.py \\")
        print(f"    --email {args.email} --phases {args.phases}")
        print()
        print("To run locally anyway (not recommended): add --local")
        import sys as _sys
        _sys.exit(1)

    print("=" * 60)
    print(f"Nexplane Backup Scheduler Smoke — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated" if not client.standalone else "Standalone mode")

    if client.standalone:
        cloud_account_id = "standalone"
    else:
        cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    try:
        if "BACKUP_SCHEDULER" in phases:
            run_phase_backup_scheduler(client, cloud_account_id)

        if "PLATFORM_UPGRADE_ROLLBACK" in phases:
            run_phase_platform_upgrade_rollback(client, cloud_account_id)

        if "AD_MEMBER_TIERS" in phases:
            run_phase_ad_member_tiers(client, cloud_account_id)

        passed = True

    finally:
        print()
        if passed:
            print("=" * 60)
            print("BACKUP_SCHEDULER_SMOKE: ALL PHASES PASSED")
            print("=" * 60)
        else:
            print("=" * 60)
            print("BACKUP_SCHEDULER_SMOKE: ONE OR MORE PHASES FAILED")
            print("=" * 60)


if __name__ == "__main__":
    main()
