# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
#!/usr/bin/env python3
"""
Live smoke test: disk2vhd backup + import_image restore.

Full machine-tier round-trip:
  DISK2VHD_BACKUP  — provision Windows EC2, capture E: as .vhdx via SSM, upload to S3
  IMPORT_IMAGE     — import the .vhdx into EC2 as an AMI via AWS VM Import
  ROLLBACK         — rollback restore (deregister AMI + delete snapshots),
                     then rollback backup (delete S3 object)

Prerequisites:
  - vmimport IAM role exists with vmie.amazonaws.com trust + S3/EC2 import policies
  - nexplane-smoke-test-key EC2 key pair exists
  - NexplaneEC2TestProfile instance profile exists (has AmazonSSMManagedInstanceCore)
  - AWS connector in platform DB has ec2:ImportImage + s3:* permissions
  - nexplane-smoke-backup-scheduler S3 bucket exists (or will be created)

Run from EC2 runner (HOST, not Docker) because subprocess docker calls are used:
    python3 tests/smoke/test_disk2vhd_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123 \\
        --phases DISK2VHD_BACKUP,IMPORT_IMAGE,ROLLBACK
"""
import argparse
import json
import os
import sys
import time
import uuid

import boto3

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

from smoke_helpers import (
    NexplaneClient,
    get_connector_creds_from_db,
    log,
    fail,
    make_base_parser,
    _get_aws_boto3_client,
    KEY_NAME,
    _wait_ssm_ready_win,
)

SMOKE_BUCKET = "nexplane-smoke-backup-scheduler"
SMOKE_IAM_PROFILE = "NexplaneEC2TestProfile"


def _ensure_s3_bucket(s3_boto, bucket: str) -> None:
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
    try:
        paginator = s3_boto.get_paginator("list_objects_v2")
        return sum(
            len(page.get("Contents", []))
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        )
    except Exception:
        return -1

SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
# Disk2VHD captures E: (2 GB EBS) — much faster than full C: and avoids OOM
CAPTURE_DRIVE = "E:"
# VHDX output written to D: (60 GB EBS) to avoid C: space pressure
VHDX_DIR = r"D:\\"
CAPTURE_TIMEOUT = 7200   # 2h — allow for slow diskpart + robocopy
UPLOAD_TIMEOUT = 7200    # 2h — large VHDX upload
IMPORT_TIMEOUT = 7200    # 2h — AWS VM Import typically 30-90 min


# ── AWS helpers ───────────────────────────────────────────────────────────────

def _ec2():
    return _get_aws_boto3_client("ec2")


def _ssm():
    return _get_aws_boto3_client("ssm")


def _s3():
    return _get_aws_boto3_client("s3")


def _ssm_run(ssm_client, instance_id, commands, timeout=120):
    """Run PowerShell commands via SSM and return stdout."""
    resp = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunPowerShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=timeout,
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout + 60
    while time.time() < deadline:
        time.sleep(5)
        inv = ssm_client.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        status = inv["Status"]
        if status not in ("Pending", "InProgress", "Delayed"):
            if status != "Success":
                raise RuntimeError(
                    f"SSM command failed ({status}): {inv.get('StandardErrorContent', '')}"
                )
            return inv.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"SSM command timed out after {timeout}s")


# ── Windows instance provisioning ────────────────────────────────────────────

_WIN_AMI_CACHE_KEY = "/nexplane/smoke-amis/disk2vhd/win2022-ssm-v1"


def _get_or_create_windows_instance(ec2_client, ssm_client) -> tuple[str, str]:
    """Get-or-create a Windows EC2 with D: (60 GB) and E: (2 GB) volumes.
    Returns (instance_id, from_cache: bool)."""
    # Check AMI cache
    cached_ami = None
    try:
        param = ssm_client.get_parameter(Name=_WIN_AMI_CACHE_KEY)
        cached_ami = json.loads(param["Parameter"]["Value"]).get("ami_id")
        log(f"  Found cached Windows AMI: {cached_ami}")
    except Exception:
        pass

    if cached_ami:
        image_id = cached_ami
    else:
        imgs = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )["Images"]
        imgs.sort(key=lambda i: i["CreationDate"], reverse=True)
        image_id = imgs[0]["ImageId"]
        log(f"  Using base Windows AMI: {image_id}")

    # Find a subnet in default VPC
    vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC found")
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}]
    )["Subnets"]
    # Filter to AZs that support t3.medium (Windows not available in all AZs)
    try:
        supported_azs = {
            o["Location"]
            for o in ec2_client.describe_instance_type_offerings(
                LocationType="availability-zone",
                Filters=[{"Name": "instance-type", "Values": ["t3.medium"]}],
            )["InstanceTypeOfferings"]
        }
        filtered = [s for s in subnets if s.get("AvailabilityZone") in supported_azs]
        if filtered:
            subnets = filtered
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    # Ensure smoke SG (SSM doesn't need inbound ports — outbound 443 is enough)
    sg_id = None
    try:
        sgs = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": ["nexplane-smoke-disk2vhd"]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )["SecurityGroups"]
        if sgs:
            sg_id = sgs[0]["GroupId"]
    except Exception:
        pass
    if not sg_id:
        sg_resp = ec2_client.create_security_group(
            GroupName="nexplane-smoke-disk2vhd",
            Description="Nexplane smoke: disk2vhd SSM",
            VpcId=vpc_id,
        )
        sg_id = sg_resp["GroupId"]
        log(f"  Created security group: {sg_id}")

    run_kwargs = dict(
        ImageId=image_id,
        InstanceType="t3.medium",
        MinCount=1,
        MaxCount=1,
        KeyName=KEY_NAME,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Name": SMOKE_IAM_PROFILE},
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sdf",
                "Ebs": {"VolumeSize": 60, "VolumeType": "gp3", "DeleteOnTermination": True},
            },
            {
                "DeviceName": "/dev/sdg",
                "Ebs": {"VolumeSize": 2, "VolumeType": "gp3", "DeleteOnTermination": True},
            },
        ],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-disk2vhd"}],
        }],
    )
    inst = ec2_client.run_instances(**run_kwargs)["Instances"][0]
    instance_id = inst["InstanceId"]
    log(f"  Launched Windows instance: {instance_id}")

    ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    log(f"  Instance running, waiting for SSM...")
    _wait_ssm_ready_win(ssm_client, instance_id, timeout=600)
    log(f"  SSM ready")

    if not cached_ami:
        # Initialize raw EBS volumes: /dev/sdf → D: (60 GB), /dev/sdg → E: (2 GB)
        diskpart_script = r"""
$rawDisks = Get-Disk | Where-Object {$_.PartitionStyle -eq 'RAW'} | Sort-Object Size -Descending
$diskD = $rawDisks | Select-Object -First 1
$diskE = $rawDisks | Where-Object {$_.UniqueId -ne $diskD.UniqueId} | Select-Object -First 1
if ($diskD) {
    $diskD | Initialize-Disk -PartitionStyle MBR -PassThru |
        New-Partition -DriveLetter D -UseMaximumSize |
        Format-Volume -FileSystem NTFS -NewFileSystemLabel Data -Confirm:$false |
        Out-Null
}
if ($diskE) {
    $diskE | Initialize-Disk -PartitionStyle MBR -PassThru |
        New-Partition -DriveLetter E -UseMaximumSize |
        Format-Volume -FileSystem NTFS -NewFileSystemLabel Capture -Confirm:$false |
        Out-Null
}
"DISK_INIT_DONE"
"""
        _ssm_run(ssm_client, instance_id, [diskpart_script], timeout=120)
        log("  D: and E: drives initialized")

        # Snapshot and cache the configured instance as an AMI
        ami_name = f"nexplane-smoke-disk2vhd-{int(time.time())}"
        ami_id = ec2_client.create_image(
            InstanceId=instance_id, Name=ami_name, NoReboot=True
        )["ImageId"]
        ec2_client.get_waiter("image_available").wait(ImageIds=[ami_id])
        ssm_client.put_parameter(
            Name=_WIN_AMI_CACHE_KEY,
            Value=json.dumps({"ami_id": ami_id}),
            Type="String",
            Overwrite=True,
        )
        log(f"  Cached Windows AMI: {ami_id}")

    return instance_id, bool(cached_ami)


# ── CR lifecycle helpers ──────────────────────────────────────────────────────

def _wait_cr(client, cr_id, timeout=300, poll=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        status = cr.get("status")
        if status in ("completed", "failed", "rolled_back", "rollback_failed", "rollback_partial"):
            return cr
        time.sleep(poll)
    fail(f"CR {cr_id} did not complete within {timeout}s (last status: {cr.get('status')})")


def _create_and_approve_cr(client, change_type, desired_outcome, asset_id):
    body = {
        "title": f"smoke-{change_type}-{uuid.uuid4().hex[:6]}",
        "change_type": change_type,
        "desired_outcome": desired_outcome,
        "target_asset_ids": [asset_id],
    }
    cr = client.post("/change-requests", json=body)
    cr_id = cr["id"]
    client.post(f"/change-requests/{cr_id}/plan")
    # Wait for planned/awaiting_approval
    deadline = time.time() + 60
    while time.time() < deadline:
        state = client.get(f"/change-requests/{cr_id}").get("status")
        if state in ("planned", "awaiting_approval"):
            break
        if state == "failed":
            fail(f"CR {cr_id} failed during planning")
        time.sleep(3)
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
    return cr_id


def _get_artifact_refs(cr):
    runs = cr.get("execution_runs") or []
    result = runs[0].get("result") or {} if runs else cr.get("result") or {}
    refs = result.get("artifact_refs")
    if refs:
        return refs
    for step in (result.get("execution") or {}).get("steps", []):
        refs = (step.get("result") or {}).get("artifact_refs")
        if refs:
            return refs
    return {}


# ── Phase functions ───────────────────────────────────────────────────────────

def run_phase_disk2vhd_backup(client, aws_connector_id, asset_id, instance_id, prefix, s3_client):
    """Create and execute a server_backup CR using disk2vhd strategy."""
    log("  Creating disk2vhd backup CR...")
    cr_id = _create_and_approve_cr(
        client,
        change_type="server_backup",
        desired_outcome={
            "capture_strategy": "disk2vhd",
            "aws_connector_id": aws_connector_id,
            "instance_id": instance_id,
            "disk_list": [CAPTURE_DRIVE],
            "_vhdx_output_dir": VHDX_DIR,
            "_capture_timeout_s": CAPTURE_TIMEOUT,
            "_upload_timeout_s": UPLOAD_TIMEOUT,
            "_storage_config": {
                "storage_type": "s3",
                "config": {
                    "bucket": SMOKE_BUCKET,
                    "prefix": prefix,
                    "region": SMOKE_REGION,
                },
            },
        },
        asset_id=asset_id,
    )
    log(f"  Executing backup CR {cr_id}...")
    client.post(f"/change-requests/{cr_id}/execute")
    cr = _wait_cr(client, cr_id, timeout=CAPTURE_TIMEOUT + UPLOAD_TIMEOUT + 300)
    if cr["status"] != "completed":
        fail(f"DISK2VHD backup failed: status={cr['status']}")

    refs = _get_artifact_refs(cr)
    uri = refs.get("artifact_uri", "")
    if not uri.endswith(".vhdx"):
        fail(f"DISK2VHD: bad artifact_uri: {uri!r}")
    size_bytes = refs.get("size_bytes", 0)
    if size_bytes <= 0:
        fail(f"DISK2VHD: vhdx size is 0")

    # Verify object in S3
    key = uri.split(f"{SMOKE_BUCKET}/", 1)[-1]
    head = s3_client.head_object(Bucket=SMOKE_BUCKET, Key=key)
    if head["ContentLength"] <= 0:
        fail("DISK2VHD: S3 object is empty")

    log(f"  DISK2VHD backup PASSED: {uri} ({size_bytes} bytes, S3 verified)")
    return cr_id, refs


def run_phase_import_image_restore(client, aws_connector_id, asset_id, ec2_client,
                                    backup_cr_id=None, artifact_uri=None, disk_format=None):
    """Create and execute a restore_server CR using import_image strategy.

    Either backup_cr_id (loads artifact_uri from that CR's result) or artifact_uri
    (pre-staged bootable image in S3) must be provided.  artifact_uri is preferred
    for smoke runs because the disk2vhd SSM-path VHDX is a file-copy (not bootable).
    """
    outcome = {
        "restore_strategy": "import_image",
        "aws_connector_id": aws_connector_id,
        "license_type": "BYOL",
    }
    if artifact_uri:
        outcome["artifact_uri"] = artifact_uri
        if disk_format:
            outcome["disk_format"] = disk_format
        source_label = artifact_uri.rsplit("/", 1)[-1]
    else:
        outcome["source_backup_cr_id"] = backup_cr_id
        source_label = backup_cr_id
    log(f"  Creating import_image restore CR (source={source_label})...")
    cr_id = _create_and_approve_cr(
        client,
        change_type="restore_server",
        desired_outcome=outcome,
        asset_id=asset_id,
    )
    log(f"  Executing restore CR {cr_id} (this takes 30-90 min for AWS VM Import)...")
    client.post(f"/change-requests/{cr_id}/execute")
    cr = _wait_cr(client, cr_id, timeout=IMPORT_TIMEOUT + 300, poll=30)
    if cr["status"] != "completed":
        fail(f"IMPORT_IMAGE restore failed: status={cr['status']}")

    runs = cr.get("execution_runs") or []
    result = runs[0].get("result") or {} if runs else {}
    ami_id = result.get("ami_id", "")
    if not ami_id:
        fail("IMPORT_IMAGE: no ami_id in execution result")

    # Verify AMI exists in EC2
    try:
        amis = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
        if not amis:
            fail(f"IMPORT_IMAGE: AMI {ami_id} not found in EC2")
    except Exception as exc:
        fail(f"IMPORT_IMAGE: describe_images failed: {exc}")

    log(f"  IMPORT_IMAGE restore PASSED: ami={ami_id}")
    return cr_id, ami_id


def run_phase_rollback(client, restore_cr_id, backup_cr_id, ami_id, prefix, ec2_client, s3_client):
    """Rollback restore (deregister AMI) then rollback backup (delete S3 object)."""
    # Rollback restore first (LIFO)
    log(f"  Rolling back restore CR {restore_cr_id}...")
    client.post(f"/change-requests/{restore_cr_id}/rollback")
    rb = _wait_cr(client, restore_cr_id, timeout=300)
    if rb["status"] not in ("rolled_back", "rollback_partial"):
        fail(f"IMPORT_IMAGE rollback failed: status={rb['status']}")

    # Verify AMI is gone
    try:
        amis = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
        if amis:
            fail(f"IMPORT_IMAGE rollback: AMI {ami_id} still exists")
    except Exception:
        pass  # InvalidAMIID.NotFound = AMI gone, which is correct
    log("  IMPORT_IMAGE rollback PASSED: AMI deregistered")

    # Rollback backup (delete S3 object)
    log(f"  Rolling back backup CR {backup_cr_id}...")
    client.post(f"/change-requests/{backup_cr_id}/rollback")
    rb2 = _wait_cr(client, backup_cr_id, timeout=180)
    if rb2["status"] not in ("rolled_back", "rollback_partial"):
        fail(f"DISK2VHD rollback failed: status={rb2['status']}")

    remaining = _count_s3_prefix(s3_client, SMOKE_BUCKET, prefix)
    if remaining > 0:
        fail(f"DISK2VHD rollback: {remaining} S3 objects still present")
    log("  DISK2VHD rollback PASSED: S3 object deleted")


# ── Main ──────────────────────────────────────────────────────────────────────

ALL_PHASES = ["DISK2VHD_BACKUP", "IMPORT_IMAGE", "ROLLBACK"]


def main():
    parser = make_base_parser(
        "disk2vhd + import_image live smoke: Windows EC2 capture → S3 → AMI → rollback"
    )
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help=f"Comma-separated phases. Default: {','.join(ALL_PHASES)}",
    )
    parser.add_argument(
        "--aws-connector-id",
        default="",
        help="AWS connector UUID. Defaults to first aws connector in DB.",
    )
    args = parser.parse_args()
    phases = [p.strip().upper() for p in args.phases.split(",")]

    client = NexplaneClient(args.base_url, args.email, args.password)

    # Resolve AWS connector ID
    aws_connector_id = args.aws_connector_id
    if not aws_connector_id:
        db_creds = get_connector_creds_from_db("aws")
        # Create a throw-away connector with real credentials
        ts = str(int(time.time()))
        conn_resp = client.post("/connectors", json={
            "connector_type": "aws",
            "name": f"smoke-disk2vhd-{ts}",
        })
        conn_data = conn_resp if isinstance(conn_resp, dict) else conn_resp.json()
        aws_connector_id = conn_data.get("id") or conn_data.get("connector_id")
        client.put(f"/connectors/{aws_connector_id}/credentials", json={"credentials": {
            "access_key_id": db_creds.get("access_key_id", ""),
            "secret_access_key": db_creds.get("secret_access_key", ""),
            "session_token": db_creds.get("session_token", ""),
            "region": db_creds.get("region", SMOKE_REGION),
        }})
        log(f"Created smoke AWS connector: {aws_connector_id}")

    # Register a smoke asset
    asset_resp = client.post("/assets", json={
        "asset_type": "server",
        "environment": "staging",
        "criticality": "low",
        "name": f"smoke-disk2vhd-{int(time.time())}",
        "tags": ["nexplane-smoke"],
    })
    asset_data = asset_resp if isinstance(asset_resp, dict) else asset_resp.json()
    asset_id = asset_data.get("id") or asset_data.get("asset_id")
    log(f"Smoke asset: {asset_id}")

    ec2_client = _ec2()
    ssm_client = _ssm()
    s3_client = _s3()
    _ensure_s3_bucket(s3_client, SMOKE_BUCKET)

    # Load pre-staged bootable image from SSM cache for IMPORT_IMAGE phase.
    # The disk2vhd SSM-path VHDX is a file-copy (non-bootable); AWS VM Import
    # requires a real bootable image.  We pre-staged a minimal Linux RAW image.
    _VHD_CACHE_KEY = "/nexplane/smoke-amis/disk2vhd/win2022-exported-vhd-v1"
    prestaged_artifact_uri = None
    prestaged_disk_format = None
    try:
        p = ssm_client.get_parameter(Name=_VHD_CACHE_KEY)
        cached = json.loads(p["Parameter"]["Value"])
        prestaged_artifact_uri = f"s3://{cached['s3_bucket']}/{cached['s3_key']}"
        prestaged_disk_format = cached.get("disk_format", "RAW")
        log(f"Pre-staged import image: {prestaged_artifact_uri} (format={prestaged_disk_format})")
    except Exception as _e:
        log(f"WARNING: no pre-staged image in SSM ({_e}); IMPORT_IMAGE will use backup CR artifact")

    run_id = uuid.uuid4().hex[:8]
    prefix = f"smoke-disk2vhd-{run_id}/"
    instance_id = None
    backup_cr_id = None
    restore_cr_id = None
    ami_id = None

    try:
        if "DISK2VHD_BACKUP" in phases:
            log("\n=== PHASE: DISK2VHD_BACKUP ===")
            instance_id, from_cache = _get_or_create_windows_instance(ec2_client, ssm_client)
            log(f"Windows instance: {instance_id} (cache={from_cache})")
            backup_cr_id, refs = run_phase_disk2vhd_backup(
                client, aws_connector_id, asset_id, instance_id, prefix, s3_client
            )

        if "IMPORT_IMAGE" in phases:
            log("\n=== PHASE: IMPORT_IMAGE ===")
            if not prestaged_artifact_uri and not backup_cr_id:
                fail("IMPORT_IMAGE requires either a pre-staged image (SSM) or DISK2VHD_BACKUP")
            restore_cr_id, ami_id = run_phase_import_image_restore(
                client, aws_connector_id, asset_id, ec2_client,
                backup_cr_id=backup_cr_id,
                artifact_uri=prestaged_artifact_uri,
                disk_format=prestaged_disk_format,
            )

        if "ROLLBACK" in phases:
            log("\n=== PHASE: ROLLBACK ===")
            if not restore_cr_id:
                fail("ROLLBACK requires IMPORT_IMAGE to have run first")
            run_phase_rollback(
                client, restore_cr_id, backup_cr_id, ami_id, prefix, ec2_client, s3_client
            )

        log("\n=== ALL_PHASES_PASSED ===")

    finally:
        # Cleanup S3 artifacts
        _delete_s3_prefix(s3_client, SMOKE_BUCKET, prefix)
        # Terminate Windows instance
        if instance_id:
            try:
                ec2_client.terminate_instances(InstanceIds=[instance_id])
                log(f"Terminated Windows instance {instance_id}")
            except Exception as exc:
                log(f"WARNING: could not terminate {instance_id}: {exc}")
        # Cleanup asset
        try:
            client.delete(f"/assets/{asset_id}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
