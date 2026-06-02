#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations  # Python 3.9 compat
import sys as _sys_enc
if hasattr(_sys_enc.stdout, 'reconfigure'):
    try:
        _sys_enc.stdout.reconfigure(encoding='utf-8', errors='replace')
        _sys_enc.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
"""
Nexplane smoke test EC2 runner.

Provisions a temporary t3.small EC2 runner, installs Python deps, copies the
test suite via SSM, and runs the tests from there. This avoids the WatchFiles
hot-reload interference that occurs when running inside the local Docker container
on Windows, and gives consistent network conditions for AWS API calls.

Usage:
    python run_on_ec2.py --phases A,AUTO_AI [--base-url http://172.31.x.x:8000] [OPTIONS]

All unknown arguments are forwarded to test_aws_live.py.
"""

import argparse
import base64
import json
import os
import sys
import time
import tempfile
import tarfile
import io
from pathlib import Path

import boto3

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RUNNER_INSTANCE_TYPE = "t3.small"
RUNNER_NAME = "nxp-ec2-test-runner"  # Intentionally different from nexplane-smoke-* to avoid backend cleanup
RUNNER_TAG = {"Key": "Name", "Value": RUNNER_NAME}
SMOKE_DIR = Path(__file__).parent            # tests/smoke/
BACKEND_DIR = SMOKE_DIR.parent.parent        # backend/

# AMI: Amazon Linux 2023 (us-east-1) — SSM agent pre-installed
AL2023_AMI = "ami-0953476d60561c955"
RUNNER_USERDATA = """#!/bin/bash
# Do NOT use set -e — a failed install step must not kill the whole userdata
# (SSM agent must stay running regardless)
dnf install -y python3-pip || true
# Core deps for the smoke test runner
pip3 install httpx boto3 || true
# Deps needed by app/ module (credential decryption helpers)
pip3 install cryptography pydantic pydantic-settings sqlalchemy 2>/dev/null || true
# Deps for standalone connector executor phases
pip3 install pymongo redis psycopg2-binary 2>/dev/null || true
# WinRM connector
pip3 install pywinrm>=0.4.3 2>/dev/null || true
# SSH connector (mac2.metal bootstrap)
pip3 install paramiko 2>/dev/null || true
echo "RUNNER_USERDATA_COMPLETE"
"""


def get_default_vpc_subnet(ec2, instance_type: str = "t3.small") -> tuple[str, str]:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC found")
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]

    # Get AZs that support this instance type
    az_info = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": [instance_type]}],
    )["InstanceTypeOfferings"]
    supported_azs = {o["Location"] for o in az_info}

    # Filter subnets to supported AZs, pick most available
    good_subnets = [s for s in subnets if s["AvailabilityZone"] in supported_azs]
    if not good_subnets:
        good_subnets = subnets  # fallback to all if none matched
    good_subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    return vpc_id, good_subnets[0]["SubnetId"]


def get_ssm_instance_profile(iam) -> str | None:
    """Return the name of an IAM instance profile whose role has SSM access.

    Prefers profiles with SSM in policy names, but also falls back to
    NexplaneEC2TestProfile which is the standard Nexplane smoke test profile.
    """
    fallback = None
    try:
        profiles = iam.list_instance_profiles(MaxItems=50)["InstanceProfiles"]
        for profile in profiles:
            pname = profile["InstanceProfileName"]
            # Track NexplaneEC2TestProfile as a fallback (it has SSM via inline/custom policy)
            if pname == "NexplaneEC2TestProfile":
                fallback = pname
            for role in profile.get("Roles", []):
                attached = iam.list_attached_role_policies(RoleName=role["RoleName"])["AttachedPolicies"]
                for p in attached:
                    if "SSM" in p["PolicyName"] or "SSM" in p["PolicyArn"]:
                        return pname
                # Also check inline policies for SSM
                try:
                    inline_names = iam.list_role_policies(RoleName=role["RoleName"])["PolicyNames"]
                    for iname in inline_names:
                        if "SSM" in iname or "ssm" in iname.lower():
                            return pname
                except Exception:
                    pass
    except Exception:
        pass
    # Return NexplaneEC2TestProfile if found — it's the designated smoke test profile
    return fallback


def write_progress_event(ssm_client, ssm_key: str, event: dict) -> None:
    """Append a progress event to the SSM parameter for live streaming.

    SSM is used here only as a transport for progress data — never to make
    changes to managed infrastructure. All infrastructure changes go through
    the Nexplane CR lifecycle.
    """
    if not ssm_key:
        return
    import json as _json
    try:
        try:
            param = ssm_client.get_parameter(Name=ssm_key)
            events = _json.loads(param["Parameter"]["Value"])
        except ssm_client.exceptions.ParameterNotFound:
            events = []
        events.append(event)
        ssm_client.put_parameter(
            Name=ssm_key,
            Value=_json.dumps(events),
            Type="String",
            Overwrite=True,
        )
    except Exception as e:
        print(f"  ⚠️  Progress write failed: {e}")


def make_test_tarball() -> bytes:
    """Package tests/smoke/ and connector executor code into a tarball for transfer.

    The full app/ directory is intentionally excluded (credential decryption helpers
    require asyncpg/DB which are not available on the runner). However, the connector
    executor subdirectories (opnsense/, step_ca/) are included so that standalone smoke
    phases can import them directly without a Nexplane backend.
    """
    buf = io.BytesIO()
    executors_dir = BACKEND_DIR / "app" / "connectors" / "executors"

    def _add_dir_safe(tar: tarfile.TarFile, src_path: Path, arcname: str) -> None:
        """Recursively add a directory to the tarball, skipping phantom entries.

        Docker Desktop bind mounts on Windows can expose phantom entries (NTFS
        artifacts) that os.scandir finds but os.lstat then fails on. We catch
        the resulting FileNotFoundError and skip those entries silently.
        """
        try:
            tar.add(str(src_path), arcname=arcname, recursive=False)
        except (FileNotFoundError, OSError) as e:
            print(f"  WARNING: skipping inaccessible tarball entry {src_path}: {e}")
            return

        if src_path.is_dir():
            try:
                entries = list(src_path.iterdir())
            except OSError:
                return
            for entry in sorted(entries):
                entry_arc = f"{arcname}/{entry.name}"
                try:
                    entry.lstat()  # probe before adding
                except OSError:
                    print(f"  WARNING: skipping inaccessible entry: {entry}")
                    continue
                _add_dir_safe(tar, entry, entry_arc)

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add smoke test files
        _add_dir_safe(tar, SMOKE_DIR, "smoke")
        # Add connector executor packages needed for standalone phases
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb",
                              "elastic", "splunk", "openvas", "nessus", "snyk", "jfrog", "winrm"):
            pkg_dir = executors_dir / connector_pkg
            if pkg_dir.exists():
                _add_dir_safe(tar, pkg_dir, f"smoke/{connector_pkg}")
    return buf.getvalue()


def launch_runner(ec2, iam, key_name: str | None = None) -> str:
    """Launch the runner EC2. Returns instance_id."""
    _, subnet_id = get_default_vpc_subnet(ec2, instance_type=RUNNER_INSTANCE_TYPE)

    launch_kwargs: dict = {
        "ImageId": AL2023_AMI,
        "InstanceType": RUNNER_INSTANCE_TYPE,
        "MinCount": 1,
        "MaxCount": 1,
        "UserData": RUNNER_USERDATA,
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [RUNNER_TAG]}],
        "NetworkInterfaces": [{
            "DeviceIndex": 0,
            "SubnetId": subnet_id,
            "AssociatePublicIpAddress": True,
        }],
    }

    ssm_profile = get_ssm_instance_profile(iam) or "NexplaneEC2TestProfile"
    launch_kwargs["IamInstanceProfile"] = {"Name": ssm_profile}

    if key_name:
        launch_kwargs["KeyName"] = key_name

    resp = ec2.run_instances(**launch_kwargs)
    return resp["Instances"][0]["InstanceId"]


def wait_for_ssm(ssm, instance_id: str, timeout: int = 600) -> None:
    """Wait until SSM agent reports the instance as online and can accept commands."""
    deadline = time.time() + timeout
    last_print = time.time()
    while time.time() < deadline:
        try:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
        except Exception as _dns_e:
            if "Name or service not known" in str(_dns_e) or "Temporary failure" in str(_dns_e) or "EndpointConnection" in str(_dns_e):
                time.sleep(10)
                continue
            raise
        if info["InstanceInformationList"]:
            item = info["InstanceInformationList"][0]
            ping = item["PingStatus"]
            if ping == "Online":
                # Probe with a real command to confirm SSM can actually execute
                # (SSM reports Online briefly before being fully ready)
                try:
                    r = ssm.send_command(
                        InstanceIds=[instance_id],
                        DocumentName="AWS-RunShellScript",
                        Parameters={"commands": ["echo ssm-ready"]},
                        TimeoutSeconds=30,  # Minimum allowed value is 30
                    )
                    cmd_id = r["Command"]["CommandId"]
                    for _ in range(10):
                        time.sleep(3)
                        try:
                            inv = ssm.get_command_invocation(
                                CommandId=cmd_id, InstanceId=instance_id)
                            if inv["Status"] in ("Success", "Failed", "Cancelled"):
                                return
                        except Exception:
                            pass
                    return  # Best-effort probe timed out — assume ready
                except Exception:
                    pass  # Not truly ready yet — keep waiting
        # Print progress every 30s
        if time.time() - last_print >= 30:
            elapsed = int(time.time() - (deadline - timeout))
            print(f"    ... waiting for SSM ({elapsed}s elapsed)", flush=True)
            last_print = time.time()
        time.sleep(10)
    raise RuntimeError(f"Runner {instance_id} never came online in SSM within {timeout}s")


def ssm_run(ssm, instance_id: str, script: str, timeout: int = 3600) -> str:
    """Run a shell script on the instance via SSM and return stdout."""
    # Retry send_command on transient network/DNS failures
    for _attempt in range(5):
        try:
            resp = ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [script]},
                TimeoutSeconds=timeout,
            )
            break
        except Exception as _e:
            _es = str(_e)
            if ("Name or service not known" in _es or "Temporary failure" in _es
                    or "EndpointConnection" in _es or "not in a valid state" in _es
                    or "InvalidInstanceId" in _es):
                if _attempt < 4:
                    time.sleep(15)
                    continue
            raise
    command_id = resp["Command"]["CommandId"]

    # Poll for completion — SSM invocation record may not exist immediately
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue  # Race condition — invocation not registered yet, retry
        status = result["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            stdout = result.get("StandardOutputContent", "")
            stderr = result.get("StandardErrorContent", "")
            if stdout:
                try:
                    print(stdout, end="")
                except UnicodeEncodeError:
                    print(stdout.encode("ascii", "replace").decode("ascii"), end="")
            if stderr:
                try:
                    print(stderr, end="", file=sys.stderr)
                except UnicodeEncodeError:
                    print(stderr.encode("ascii", "replace").decode("ascii"), end="", file=sys.stderr)
            if status != "Success":
                raise RuntimeError(f"SSM command failed with status: {status}")
            return stdout
        # Print progress dots
        print(".", end="", flush=True)

    raise RuntimeError(f"SSM command timed out after {timeout}s")


def ssm_run_with_retry(ssm, instance_id: str, script: str, retries: int = 5,
                       timeout: int = 60) -> str:
    """Run an SSM command with retry for transient 'Undeliverable' / 'InvalidInstanceId' failures."""
    import botocore.exceptions as _bce
    last_exc = None
    for attempt in range(retries):
        try:
            return ssm_run(ssm, instance_id, script, timeout=timeout)
        except (_bce.ClientError, RuntimeError) as e:
            last_exc = e
            err_str = str(e)
            transient = (
                "InvalidInstanceId" in err_str
                or "Undeliverable" in err_str
                or "Failed" in err_str
                or "not in a valid state" in err_str
            )
            if transient and attempt < retries - 1:
                wait_secs = 15 * (attempt + 1)
                print(f"\n  SSM transient failure (attempt {attempt+1}/{retries}), "
                      f"waiting {wait_secs}s before retry...")
                time.sleep(wait_secs)
                continue
            raise
    raise last_exc  # type: ignore[misc]


def transfer_files(ssm, instance_id: str, tarball: bytes) -> None:
    """Transfer test files to the runner via S3 pre-signed URL (fast, no IAM needed on runner).

    Uploads the tarball to S3 from the local machine (which has full AWS creds),
    generates a pre-signed URL valid for 30 min, then downloads on the runner via curl.
    Falls back to SSM chunked base64 transfer if S3 is unavailable.
    """
    import boto3 as _boto3
    import uuid as _uuid

    # Wait for userdata to settle (userdata installs pip packages; SSM may flicker briefly)
    time.sleep(45)

    # --- Primary path: S3 pre-signed URL ---
    s3_key = f"nexplane-smoke-runner/{_uuid.uuid4().hex}/smoke.tar.gz"
    try:
        s3 = _boto3.client("s3", region_name="us-east-1")
        # Use nexplane-agent-downloads bucket (presigned URLs work from any EC2)
        bucket = "nexplane-agent-downloads"
        print(f"  Uploading {len(tarball)//1024}KB to s3://{bucket}...")
        s3.put_object(Bucket=bucket, Key=s3_key, Body=tarball,
                      ContentType="application/gzip")
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=1800,
        )
        download_cmd = (
            f"curl -fsSL -o /tmp/smoke.tar.gz '{presigned_url}' && "
            "mkdir -p /tmp/nexplane_smoke && "
            "tar -xzf /tmp/smoke.tar.gz -C /tmp/nexplane_smoke && "
            "echo 'Files extracted successfully'"
        )
        ssm_run_with_retry(ssm, instance_id, download_cmd, retries=5, timeout=120)
        try:
            s3.delete_object(Bucket=bucket, Key=s3_key)
        except Exception:
            pass
        print("  S3 transfer complete")
        return
    except Exception as s3_e:
        print(f"  S3 transfer failed ({type(s3_e).__name__}: {s3_e}), falling back to SSM chunked...")

    # --- Fallback: SSM chunked base64 transfer ---
    b64 = base64.b64encode(tarball).decode()
    # 8192 chars worked reliably in prior runs
    chunk_size = 8192
    chunks = [b64[i:i+chunk_size] for i in range(0, len(b64), chunk_size)]
    print(f"  SSM-chunked transfer: {len(tarball)//1024}KB in {len(chunks)} chunk(s)...")

    ssm_run_with_retry(ssm, instance_id, f"echo -n '{chunks[0]}' > /tmp/smoke_b64.txt")
    for i, chunk in enumerate(chunks[1:], 1):
        ssm_run_with_retry(ssm, instance_id, f"echo -n '{chunk}' >> /tmp/smoke_b64.txt")
        if i % 20 == 0:
            print(f"  ... {i}/{len(chunks)-1} chunks done")

    ssm_run(ssm, instance_id,
            "base64 -d /tmp/smoke_b64.txt > /tmp/smoke.tar.gz && "
            "mkdir -p /tmp/nexplane_smoke && "
            "tar -xzf /tmp/smoke.tar.gz -C /tmp/nexplane_smoke && "
            "echo 'Files extracted successfully'")


def get_or_create_smoke_ami(ssm_client, ec2_client, instance_id: str, ami_name: str,
                             setup_script_hash: str) -> str | None:
    """
    After provisioning and configuring a smoke test EC2, snapshot it as an AMI.
    On future runs, return the cached AMI ID instead of re-provisioning.

    Cache key: /nexplane/smoke-amis/{ami_name}/{setup_script_hash[:8]}
    Returns AMI ID if created successfully, None on failure.
    """
    param_path = f"/nexplane/smoke-amis/{ami_name}/{setup_script_hash[:8]}"

    # Check if cached AMI exists
    try:
        resp = ssm_client.get_parameter(Name=param_path)
        cached_ami_id = resp["Parameter"]["Value"]
        # Verify the AMI still exists and is available
        try:
            amis = ec2_client.describe_images(ImageIds=[cached_ami_id])
            if amis["Images"] and amis["Images"][0]["State"] == "available":
                print(f"  Using cached smoke AMI: {cached_ami_id} ({ami_name})")
                return cached_ami_id
        except Exception:
            pass  # AMI gone — recreate
    except ssm_client.exceptions.ParameterNotFound:
        pass

    # Create new AMI from running instance
    try:
        print(f"  Creating smoke AMI snapshot: {ami_name} (first-run setup complete)")
        resp = ec2_client.create_image(
            InstanceId=instance_id,
            Name=f"nexplane-smoke-{ami_name}-{setup_script_hash[:8]}",
            Description=f"Nexplane smoke test: {ami_name} pre-configured",
            NoReboot=True,  # Don't reboot — test will continue using this instance
        )
        ami_id = resp["ImageId"]

        # Store in SSM
        ssm_client.put_parameter(
            Name=param_path,
            Value=ami_id,
            Type="String",
            Overwrite=True,
        )
        print(f"  AMI {ami_id} created and cached at {param_path}")
        return ami_id
    except Exception as e:
        print(f"  WARNING: AMI snapshot failed (non-fatal): {e}")
        return None


def launch_from_smoke_ami(ec2_client, ami_id: str, subnet_id: str,
                           security_group_id: str, instance_profile: str) -> str | None:
    """Launch a pre-configured smoke test EC2 from a cached AMI."""
    try:
        resp = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType="t3.small",
            MinCount=1, MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=[security_group_id],
            IamInstanceProfile={"Name": instance_profile},
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-from-ami"},
                {"Key": "nexplane-smoke", "Value": "true"},
            ]}],
        )
        return resp["Instances"][0]["InstanceId"]
    except Exception as e:
        print(f"  WARNING: Launch from AMI failed: {e}")
        return None


def terminate_runner(ec2, instance_id: str) -> None:
    """Terminate the runner instance with retry on transient DNS/network errors."""
    for attempt in range(5):
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  Runner {instance_id} terminated")
            return
        except Exception as e:
            es = str(e)
            if attempt < 4 and ("Name or service not known" in es
                    or "Temporary failure" in es or "EndpointConnection" in es):
                time.sleep(10)
                continue
            print(f"  ⚠️  Could not terminate runner {instance_id}: {e}")
            return


def setup_backend_tailscale(auth_key: str) -> str:
    """Join the backend container to Tailscale and return its Tailscale IP.

    run_on_ec2.py runs inside the backend Docker container, which has tailscale
    installed. Joining here makes the backend reachable from the runner EC2.
    """
    import subprocess
    print("  Starting tailscaled daemon (userspace networking)...")
    # Start tailscaled if not already running; ignore errors if already up
    subprocess.Popen(
        ["tailscaled", "--tun=userspace-networking"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(5)  # give tailscaled time to come up

    print("  Joining backend to Tailscale...")
    subprocess.run(
        ["tailscale", "up",
         f"--authkey={auth_key}",
         "--hostname=nexplane-backend",
         "--accept-routes",
         "--accept-dns=false"],
        check=True, capture_output=True,
    )
    result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True)
    ip = result.stdout.strip()
    print(f"  Backend Tailscale IP: {ip}")
    return ip


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Nexplane smoke tests from a temporary EC2 runner instance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script provisions a t3.small EC2 runner, transfers the smoke test suite,
and runs the tests from there. This avoids hot-reload interference from the
local Docker environment on Windows.

All arguments after known flags are forwarded to test_aws_live.py.

Examples:
    python run_on_ec2.py --email admin@acme.example --password admin123 --phases A,AUTO_AI
    python run_on_ec2.py --email admin@acme.example --password admin123 --phases A,AUTO_AI \\
        --base-url http://172.31.x.x:8000
""",
    )
    parser.add_argument("--base-url", default="",
                        help="Backend URL. Defaults to platform VPC private IP via IMDS.")
    parser.add_argument("--email", default="admin@acme.example",
                        help="Nexplane user email (default: admin@acme.example)")
    parser.add_argument("--password", default="admin123",
                        help="Nexplane user password (default: admin123)")
    parser.add_argument("--phases", default="A,AUTO_AI", help="Comma-separated phases")
    parser.add_argument("--tailscale-auth-key", default="",
                        help="Tailscale reusable auth key (required if phases include A). "
                             "If not provided, auto-fetched from the Tailscale connector in the platform DB.")
    parser.add_argument("--run-id", default="",
                        help="SmokeTestRun UUID — enables SSM progress streaming and DB state updates")
    parser.add_argument("--agent-asset-id", default="",
                        help="Pre-provisioned agent endpoint asset ID (from Phase A output)")
    parser.add_argument("--ec2-instance-id", default="",
                        help="EC2 instance ID with agent installed (for SSM side-effect verification)")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--keep-runner", action="store_true",
                        help="Do not terminate the runner EC2 after the test")
    parser.add_argument("--tarball-path", default="",
                        help="Path to pre-built tarball (skips make_test_tarball(); "
                             "use when Docker bind mount 9P is slow/stuck)")

    args, extra = parser.parse_known_args()

    # Resolve base URL: use VPC private IP from IMDS if not explicitly set.
    # Runners are in the same VPC as the platform — no Tailscale needed.
    if not args.base_url:
        try:
            import urllib.request as _ur
            _imds_token = _ur.urlopen(
                _ur.Request("http://169.254.169.254/latest/api/token",
                            headers={"X-aws-ec2-metadata-token-ttl-seconds": "10"},
                            method="PUT"), timeout=2
            ).read().decode()
            _private_ip = _ur.urlopen(
                _ur.Request("http://169.254.169.254/latest/meta-data/local-ipv4",
                            headers={"X-aws-ec2-metadata-token": _imds_token}), timeout=2
            ).read().decode()
            args.base_url = f"http://{_private_ip}:8000"
            print(f"Platform base URL (VPC): {args.base_url}")
        except Exception as _e:
            args.base_url = "http://localhost:8000"
            print(f"IMDS unavailable ({_e}), falling back to {args.base_url}")

    # Auto-fetch Tailscale auth key from platform DB if not provided
    if not args.tailscale_auth_key:
        try:
            import asyncio as _asyncio, sys as _sys
            if "/app" not in _sys.path:
                _sys.path.insert(0, "/app")
            from app.config import settings as _cfg
            from app.services.secrets_service import SecretsService as _Secrets
            from app.models.connector import Connector as _Connector
            from app.models.connector_credential import ConnectorCredential as _CC
            from sqlalchemy import select as _select
            from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AS
            from sqlalchemy.orm import sessionmaker as _sm

            async def _fetch_ts_key():
                engine = create_async_engine(_cfg.DATABASE_URL, pool_pre_ping=False)
                Session = _sm(engine, class_=_AS, expire_on_commit=False)
                async with Session() as db:
                    row = await db.execute(_select(_Connector).where(_Connector.connector_type == "tailscale"))
                    conn = row.scalar_one_or_none()
                    if not conn:
                        return ""
                    cred_row = await db.execute(_select(_CC).where(_CC.connector_id == conn.id))
                    cred = cred_row.scalar_one_or_none()
                    if not cred:
                        return ""
                    svc = _Secrets(_cfg.SECRET_KEY)
                    return svc.decrypt_json(cred.credentials_encrypted).get("auth_key", "")

            _key = _asyncio.run(_fetch_ts_key())
            if _key:
                args.tailscale_auth_key = _key
                print(f"  Tailscale auth key auto-fetched from platform DB")
        except Exception as _e:
            print(f"  Could not auto-fetch Tailscale auth key: {_e}")

    # Auto-fetch AWS credentials from platform DB if not in environment
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        try:
            import asyncio as _asyncio2, sys as _sys2
            if "/app" not in _sys2.path:
                _sys2.path.insert(0, "/app")
            from app.config import settings as _cfg2
            from app.services.secrets_service import SecretsService as _Secrets2
            from app.models.connector import Connector as _Connector2
            from app.models.connector_credential import ConnectorCredential as _CC2
            from sqlalchemy import select as _select2
            from sqlalchemy.ext.asyncio import create_async_engine as _cae2, AsyncSession as _AS2
            from sqlalchemy.orm import sessionmaker as _sm2

            async def _fetch_aws_creds():
                engine = _cae2(_cfg2.DATABASE_URL, pool_pre_ping=False)
                Session = _sm2(engine, class_=_AS2, expire_on_commit=False)
                async with Session() as db:
                    row = await db.execute(_select2(_Connector2).where(_Connector2.connector_type == "aws"))
                    conn = row.scalar_one_or_none()
                    if not conn:
                        return {}
                    cred_row = await db.execute(_select2(_CC2).where(_CC2.connector_id == conn.id))
                    cred = cred_row.scalar_one_or_none()
                    if not cred:
                        return {}
                    svc = _Secrets2(_cfg2.SECRET_KEY)
                    return svc.decrypt_json(cred.credentials_encrypted)

            _aws = _asyncio2.run(_fetch_aws_creds())
            if _aws.get("access_key_id"):
                os.environ["AWS_ACCESS_KEY_ID"] = _aws["access_key_id"]
                os.environ["AWS_SECRET_ACCESS_KEY"] = _aws.get("secret_access_key", "")
                if _aws.get("region"):
                    args.region = _aws["region"]
                print("  AWS credentials auto-fetched from platform DB")
        except Exception as _e2:
            print(f"  Could not auto-fetch AWS credentials: {_e2}")

    # Auto-fetch SaaS connector credentials from platform DB and inject as env vars.
    # The runner EC2 doesn't have the app/ module, so credentials must be passed as env vars.
    _saas_env_extra = ""
    _SAAS_CONNECTOR_TYPES = ["oci", "azure_ad", "defender_endpoint", "okta", "pagerduty",
                              "servicenow", "snyk", "gcp"]
    try:
        import asyncio as _asyncio_saas, base64 as _b64, json as _json_saas, sys as _sys_saas
        if "/app" not in _sys_saas.path:
            _sys_saas.path.insert(0, "/app")
        from app.config import settings as _cfg_saas
        from app.models.connector import Connector as _ConnSaas
        from app.models.connector_credential import ConnectorCredential as _CCSaas
        from app.services.secrets_service import SecretsService as _SecSaas
        from sqlalchemy import select as _sel_saas
        from sqlalchemy.ext.asyncio import create_async_engine as _cae_saas, AsyncSession as _AS_saas
        from sqlalchemy.orm import sessionmaker as _sm_saas

        async def _fetch_saas_creds():
            engine = _cae_saas(_cfg_saas.DATABASE_URL, pool_pre_ping=False)
            Session = _sm_saas(engine, class_=_AS_saas, expire_on_commit=False)
            result = {}
            svc = _SecSaas(_cfg_saas.SECRET_KEY)
            async with Session() as db:
                for _ct in _SAAS_CONNECTOR_TYPES:
                    row = await db.execute(_sel_saas(_ConnSaas).where(_ConnSaas.connector_type == _ct))
                    conn = row.scalars().first()
                    if not conn:
                        continue
                    cred_row = await db.execute(_sel_saas(_CCSaas).where(_CCSaas.connector_id == conn.id))
                    cred = cred_row.scalar_one_or_none()
                    if not cred:
                        continue
                    try:
                        result[_ct] = svc.decrypt_json(cred.credentials_encrypted)
                    except Exception:
                        pass
            await engine.dispose()
            return result

        _saas_creds = _asyncio_saas.run(_fetch_saas_creds())
        for _ct, _creds in _saas_creds.items():
            _env_key = f"NEXPLANE_CREDS_{_ct.upper().replace('-', '_')}"
            _encoded = _b64.b64encode(_json_saas.dumps(_creds).encode()).decode()
            _saas_env_extra += f"{_env_key}={_encoded} "
            print(f"  SaaS creds injected for connector_type={_ct}")
    except Exception as _saas_e:
        print(f"  SaaS credential injection skipped: {_saas_e}")

    region = args.region
    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    iam = boto3.client("iam", region_name=region)

    runner_id = None
    exit_code = 1

    try:
        # Package test files
        if args.tarball_path:
            print(f"Using pre-built tarball: {args.tarball_path}")
            with open(args.tarball_path, "rb") as _tf:
                tarball = _tf.read()
        else:
            print("Packaging smoke test files...")
            tarball = make_test_tarball()

        backend_ts_ip = args.base_url.split("//")[-1].split(":")[0]
        _is_vpc_ip = not backend_ts_ip.startswith("100.")

        if args.tailscale_auth_key and _is_vpc_ip:
            # Platform SG blocks all VPC inbound — runner must reach platform via Tailscale.
            # Discover the host's Tailscale IP by probing online peers from the container's
            # tailscale daemon (the host appears as a peer at 100.101.186.39 / nexplane-dev).
            try:
                import subprocess as _sp, json as _js, urllib.request as _ur3
                _ts_out = _sp.run(
                    ["tailscale", "--socket=/var/run/tailscale/tailscaled.sock",
                     "status", "--json"],
                    capture_output=True, text=True, timeout=5,
                )
                if _ts_out.returncode == 0:
                    _ts_data = _js.loads(_ts_out.stdout)
                    _candidates = [
                        ip
                        for peer in _ts_data.get("Peer", {}).values()
                        if peer.get("Online")
                        for ip in peer.get("TailscaleIPs", [])
                        if ip.startswith("100.")
                    ]
                    for _cip in _candidates:
                        try:
                            _resp = _ur3.urlopen(
                                f"http://{_cip}:8000/health", timeout=3
                            )
                            if _resp.status == 200:
                                backend_ts_ip = _cip
                                args.base_url = f"http://{_cip}:8000"
                                print(f"  Tailscale backend discovered at {_cip} (peer probe)")
                                break
                        except Exception:
                            continue
                    else:
                        print(f"  ⚠️  No Tailscale peer has port 8000 — runner will use VPC URL")
            except Exception as _tse:
                print(f"  ⚠️  Tailscale peer probe failed: {_tse} — using {args.base_url}")
        elif args.tailscale_auth_key and not _is_vpc_ip:
            try:
                backend_ts_ip = setup_backend_tailscale(args.tailscale_auth_key)
                args.base_url = f"http://{backend_ts_ip}:8000"
            except Exception as e:
                print(f"  ⚠️  Could not join backend to Tailscale: {e} — using {args.base_url}")

        # Terminate any orphaned runners from previous aborted runs before launching.
        # These accumulate when the platform process dies mid-run (e.g. crash) and
        # the finally-block cleanup never executes. Hitting vCPU limits is the symptom.
        try:
            _orphans = ec2.describe_instances(Filters=[
                {"Name": "tag:Name", "Values": [RUNNER_NAME]},
                {"Name": "instance-state-name", "Values": ["running", "pending", "stopping"]},
            ])["Reservations"]
            _orphan_ids = [i["InstanceId"] for r in _orphans for i in r["Instances"]]
            if _orphan_ids:
                print(f"  Terminating {len(_orphan_ids)} orphaned runner(s): {_orphan_ids}")
                ec2.terminate_instances(InstanceIds=_orphan_ids)
        except Exception as _oe:
            print(f"  Orphan cleanup skipped: {_oe}")

        # Launch runner
        print(f"Launching {RUNNER_INSTANCE_TYPE} runner EC2...")
        runner_id = launch_runner(ec2, iam)
        print(f"  Runner: {runner_id}")

        # Update SmokeTestRun with runner instance ID if run_id provided
        if args.run_id:
            try:
                import sys as _sys
                if "/app" not in _sys.path:
                    _sys.path.insert(0, "/app")
                import asyncio as _asyncio
                from app.config import settings as _cfg
                from app.models.smoke_test_run import SmokeTestRun as _STR
                from sqlalchemy import select as _select
                from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AS
                from sqlalchemy.orm import sessionmaker as _sm
                import uuid as _uuid

                async def _update_runner():
                    engine = create_async_engine(_cfg.DATABASE_URL)
                    Session = _sm(engine, class_=_AS, expire_on_commit=False)
                    async with Session() as db:
                        r = await db.execute(_select(_STR).where(_STR.id == _uuid.UUID(args.run_id)))
                        run = r.scalar_one_or_none()
                        if run:
                            run.runner_instance_id = runner_id
                            await db.commit()
                    await engine.dispose()

                _asyncio.run(_update_runner())
            except Exception as _e:
                print(f"  ⚠️  Could not update run record: {_e}")

        # Wait for instance to pass status checks — manual loop to handle transient DNS failures
        print("  Waiting for instance to pass status checks (up to 20 min)...")
        _status_deadline = time.time() + 1200  # 20 min max
        _status_ok = False
        while time.time() < _status_deadline:
            try:
                # Check instance state first — detect early termination
                _desc = ec2.describe_instances(InstanceIds=[runner_id])
                _state = _desc["Reservations"][0]["Instances"][0]["State"]["Name"]
                if _state in ("terminated", "stopped", "shutting-down"):
                    raise RuntimeError(f"Runner {runner_id} entered unexpected state: {_state}")
                _st = ec2.describe_instance_status(InstanceIds=[runner_id])
                _statuses = _st.get("InstanceStatuses", [])
                if _statuses:
                    _is = _statuses[0]
                    if (_is["InstanceStatus"]["Status"] == "ok"
                            and _is["SystemStatus"]["Status"] == "ok"):
                        _status_ok = True
                        break
            except RuntimeError:
                raise
            except Exception as _e:
                if any(kw in str(_e) for kw in ("Name or service not known", "Temporary failure",
                                                     "EndpointConnection", "InvalidInstanceID.NotFound",
                                                     "does not exist")):
                    print(f"  ... transient error, retrying: {type(_e).__name__}", flush=True)
                else:
                    raise
            elapsed = int(time.time() - (_status_deadline - 1200))
            print(f"  ... waiting for status checks ({elapsed}s elapsed)...", flush=True)
            time.sleep(15)
        if not _status_ok:
            raise RuntimeError(f"Runner {runner_id} never passed status checks within 1200s")

        # Wait for SSM — AL2023 userdata installs pip packages which can take ~3-5 min
        print("  Waiting for SSM agent (up to 10 min)...")
        wait_for_ssm(ssm, runner_id, timeout=600)
        print("  Runner ready")

        # Transfer test suite
        transfer_files(ssm, runner_id, tarball)

        # Build the test command
        # Pass AWS creds as env vars so _get_aws_boto3_client() doesn't need asyncpg/DB
        import os as _os
        aws_key = _os.environ.get("AWS_ACCESS_KEY_ID", "")
        aws_secret = _os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        aws_region = _os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        _ts_key_env = args.tailscale_auth_key or ""
        aws_env = (
            f"AWS_ACCESS_KEY_ID={aws_key} "
            f"AWS_SECRET_ACCESS_KEY={aws_secret} "
            f"AWS_DEFAULT_REGION={aws_region} "
            f"NEXPLANE_BACKEND_TAILSCALE_IP={backend_ts_ip} "
            + (f"TAILSCALE_AUTH_KEY={_ts_key_env} " if _ts_key_env else "")
            + _saas_env_extra
        )
        # Build test command — email/password are optional for standalone phases
        _email_arg = f" --email {args.email}" if args.email else ""
        _password_arg = f" --password {args.password}" if args.password else ""
        PLATFORM_PHASES = {
            "IR_ISOLATE_HOST", "IR_PRESERVE_EVIDENCE", "IR_LOCKDOWN_ACCOUNT", "IR_PHISHING_RESPONSE",
            "RUNBOOK_ONBOARDING", "RUNBOOK_ACCOUNT_COMPROMISE", "RUNBOOK_PATCH_CAMPAIGN",
            "ACCESS_REVIEW", "PROJECT_MICROSEG", "VULN_PIPELINE",
        }
        selected_phases = set(args.phases.split(","))
        if selected_phases & PLATFORM_PHASES:
            test_script = "test_platform_live.py"
        else:
            test_script = "test_aws_live.py"
        test_cmd_parts = [
            "cd /tmp/nexplane_smoke",
            f"{aws_env}NEXPLANE_RUNNER_EC2=1 PYTHONPATH=/tmp/nexplane_smoke python3 smoke/{test_script}"
            f" --base-url {args.base_url}"
            f"{_email_arg}"
            f"{_password_arg}"
            f" --phases {args.phases}"
            f" --backend-tailscale-ip {backend_ts_ip}",
        ]
        if args.tailscale_auth_key:
            test_cmd_parts[-1] += f" --tailscale-auth-key {args.tailscale_auth_key}"
        if args.run_id:
            test_cmd_parts[-1] += f" --run-id {args.run_id}"
        if args.agent_asset_id:
            test_cmd_parts[-1] += f" --agent-asset-id {args.agent_asset_id}"
        if args.ec2_instance_id:
            test_cmd_parts[-1] += f" --ec2-instance-id {args.ec2_instance_id}"
        for ex in extra:
            test_cmd_parts[-1] += f" {ex}"

        # Always install Tailscale on runner when a tailscale auth key is provided,
        # since the backend is always reachable only via Tailscale.
        if args.tailscale_auth_key:
            print("  Installing Tailscale on runner...")
            ssm_run(ssm, runner_id,
                    # install.sh may return non-zero on some AL2023 versions — ignore it
                    "curl -fsSL https://tailscale.com/install.sh | sh || true && "
                    "systemctl enable --now tailscaled 2>/dev/null || true && sleep 3 && "
                    f"tailscale up --authkey={args.tailscale_auth_key} "
                    "--hostname=nexplane-smoke-runner --accept-routes --accept-dns=false")

        test_script = " && ".join(test_cmd_parts)
        print(f"\n{'='*60}")
        print(f"Running smoke test on {runner_id}")
        print(f"Phases: {args.phases}")
        print(f"{'='*60}\n")

        # Run the test in the background (SSM TimeoutSeconds max is 2800 ~47 min,
        # but multi-phase runs can take 60-90+ min). We launch via nohup and poll.
        # Run test in background using nohup+bash. The watchdog (smoke-watchdog
        # tmux session) will create smoke_done if bash doesn't for any reason.
        bg_launch_script = (
            # Use bash explicitly (not sh/dash) so PIPESTATUS is available.
            f"nohup bash -c '{test_script} 2>&1 | tee /tmp/smoke_test.log; "
            "_ec=${{PIPESTATUS[0]}}; "
            "echo SMOKE_EXIT_CODE:$_ec >> /tmp/smoke_test.log; "
            "touch /tmp/smoke_done' </dev/null >/dev/null 2>&1 &\n"
            "echo LAUNCHED:$$"
        )
        try:
            ssm_run(ssm, runner_id, bg_launch_script, timeout=30)
            print("  Test launched in background, polling for completion...")
            # Poll /tmp/smoke_done + tail log every 30s, up to 7200s total
            out = ""
            poll_deadline = time.time() + 7200
            last_log_size = 0
            while time.time() < poll_deadline:
                time.sleep(30)
                # Stream new log lines
                try:
                    log_out = ssm_run(ssm, runner_id,
                        f"tail -c +{last_log_size + 1} /tmp/smoke_test.log 2>/dev/null | head -c 4096",
                        timeout=30)
                    if log_out:
                        print(log_out, end="", flush=True)
                        last_log_size += len(log_out)
                        out += log_out
                except Exception:
                    pass
                # Check if done
                try:
                    done_out = ssm_run(ssm, runner_id, "test -f /tmp/smoke_done && echo DONE", timeout=30)
                    if "DONE" in done_out:
                        # Fetch rest of log
                        try:
                            rest = ssm_run(ssm, runner_id,
                                f"tail -c +{last_log_size + 1} /tmp/smoke_test.log 2>/dev/null",
                                timeout=60)
                            if rest:
                                print(rest, end="", flush=True)
                                out += rest
                        except Exception:
                            pass
                        break
                except Exception:
                    pass
                print(".", end="", flush=True)
            else:
                raise RuntimeError("Smoke test polling timed out after 7200s")
            # Check if the test itself failed (exit code embedded in output)
            if "SMOKE_EXIT_CODE:0" not in out:
                # Retrieve full log if available
                try:
                    full_log = ssm_run(ssm, runner_id,
                                       "tail -200 /tmp/smoke_test.log 2>/dev/null || true",
                                       timeout=30)
                    if full_log:
                        print("\n--- Full test log (last 200 lines) ---")
                        print(full_log)
                        print("--- End test log ---")
                except Exception:
                    pass
                raise RuntimeError("Smoke test exited with non-zero status")
            exit_code = 0
        except RuntimeError as e:
            print(f"\n❌ Smoke test failed on runner: {e}")
            exit_code = 1

    except KeyboardInterrupt:
        print("\nInterrupted")
        exit_code = 130
    except Exception as e:
        print(f"\n❌ Runner setup failed: {e}")
        import traceback; traceback.print_exc()
        exit_code = 1
    finally:
        if runner_id and not args.keep_runner:
            terminate_runner(ec2, runner_id)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
