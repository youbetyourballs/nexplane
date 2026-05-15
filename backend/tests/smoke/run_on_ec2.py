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
    python run_on_ec2.py --phases A,AUTO_AI [--base-url http://100.x.x.x:8000] [OPTIONS]

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
RUNNER_NAME = "nexplane-smoke-runner"
RUNNER_TAG = {"Key": "Name", "Value": RUNNER_NAME}
SMOKE_DIR = Path(__file__).parent            # tests/smoke/
BACKEND_DIR = SMOKE_DIR.parent.parent        # backend/

# AMI: Amazon Linux 2023 (us-east-1) — SSM agent pre-installed
AL2023_AMI = "ami-0953476d60561c955"
RUNNER_USERDATA = """#!/bin/bash
set -e
dnf install -y python3-pip
# Core deps for the smoke test runner
pip3 install httpx boto3
# Deps needed by app/ module (credential decryption helpers)
pip3 install cryptography pydantic pydantic-settings sqlalchemy 2>/dev/null || true
# Deps for standalone connector executor phases
pip3 install pymongo redis psycopg2-binary 2>/dev/null || true
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
    """Return the name of an IAM instance profile whose role has SSM access."""
    try:
        profiles = iam.list_instance_profiles(MaxItems=50)["InstanceProfiles"]
        for profile in profiles:
            for role in profile.get("Roles", []):
                attached = iam.list_attached_role_policies(RoleName=role["RoleName"])["AttachedPolicies"]
                for p in attached:
                    if "SSM" in p["PolicyName"] or "SSM" in p["PolicyArn"]:
                        return profile["InstanceProfileName"]
    except Exception:
        pass
    return None


def make_test_tarball() -> bytes:
    """Package tests/smoke/ and connector executor code into a tarball for transfer.

    The full app/ directory is intentionally excluded (credential decryption helpers
    require asyncpg/DB which are not available on the runner). However, the connector
    executor subdirectories (opnsense/, step_ca/) are included so that standalone smoke
    phases can import them directly without a Nexplane backend.
    """
    buf = io.BytesIO()
    executors_dir = BACKEND_DIR / "app" / "connectors" / "executors"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add smoke test files
        tar.add(SMOKE_DIR, arcname="smoke")
        # Add connector executor packages needed for standalone phases
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb",
                              "elastic", "splunk"):
            pkg_dir = executors_dir / connector_pkg
            if pkg_dir.exists():
                tar.add(pkg_dir, arcname=f"smoke/{connector_pkg}")
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

    ssm_profile = get_ssm_instance_profile(iam)
    if ssm_profile:
        launch_kwargs["IamInstanceProfile"] = {"Name": ssm_profile}
    else:
        print("  ⚠️  No SSM-capable IAM instance profile found — SSM commands may fail")

    if key_name:
        launch_kwargs["KeyName"] = key_name

    resp = ec2.run_instances(**launch_kwargs)
    return resp["Instances"][0]["InstanceId"]


def wait_for_ssm(ssm, instance_id: str, timeout: int = 300) -> None:
    """Wait until SSM agent reports the instance as online."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        if info["InstanceInformationList"]:
            item = info["InstanceInformationList"][0]
            if item["PingStatus"] == "Online":
                return
        time.sleep(10)
    raise RuntimeError(f"Runner {instance_id} never came online in SSM within {timeout}s")


def ssm_run(ssm, instance_id: str, script: str, timeout: int = 3600) -> str:
    """Run a shell script on the instance via SSM and return stdout."""
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script]},
        TimeoutSeconds=timeout,
    )
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


def transfer_files(ssm, instance_id: str, tarball: bytes) -> None:
    """Transfer test files to the runner via SSM (base64 encoded)."""
    b64 = base64.b64encode(tarball).decode()
    # Split into chunks to avoid SSM document size limits (16KB per command)
    chunk_size = 8192
    chunks = [b64[i:i+chunk_size] for i in range(0, len(b64), chunk_size)]

    print(f"  Transferring {len(tarball)//1024}KB in {len(chunks)} chunk(s)...")

    # Write first chunk (create file)
    ssm_run(ssm, instance_id, f"echo -n '{chunks[0]}' > /tmp/smoke_b64.txt")

    # Append remaining chunks
    for chunk in chunks[1:]:
        ssm_run(ssm, instance_id, f"echo -n '{chunk}' >> /tmp/smoke_b64.txt")

    # Decode and extract
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
    try:
        ec2.terminate_instances(InstanceIds=[instance_id])
        print(f"  Runner {instance_id} terminated")
    except Exception as e:
        print(f"  ⚠️  Could not terminate runner {instance_id}: {e}")


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
        --base-url http://100.122.229.11:8000
""",
    )
    parser.add_argument("--base-url", default="http://100.122.229.11:8000",
                        help="Backend URL (default: Tailscale IP)")
    parser.add_argument("--email", default="", help="Nexplane user email (optional for standalone phases)")
    parser.add_argument("--password", default="", help="Nexplane user password (optional for standalone phases)")
    parser.add_argument("--phases", default="A,AUTO_AI", help="Comma-separated phases")
    parser.add_argument("--tailscale-auth-key", default="",
                        help="Tailscale reusable auth key (required if phases include A)")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--keep-runner", action="store_true",
                        help="Do not terminate the runner EC2 after the test")

    args, extra = parser.parse_known_args()

    region = args.region
    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    iam = boto3.client("iam", region_name=region)

    runner_id = None
    exit_code = 1

    try:
        # Package test files
        print("Packaging smoke test files...")
        tarball = make_test_tarball()

        # Join backend to Tailscale so the runner can reach it
        backend_ts_ip = args.base_url.split("//")[-1].split(":")[0]
        if args.tailscale_auth_key:
            try:
                backend_ts_ip = setup_backend_tailscale(args.tailscale_auth_key)
                # Override base_url to use the fresh Tailscale IP
                args.base_url = f"http://{backend_ts_ip}:8000"
            except Exception as e:
                print(f"  ⚠️  Could not join backend to Tailscale: {e} — using {args.base_url}")

        # Launch runner
        print(f"Launching {RUNNER_INSTANCE_TYPE} runner EC2...")
        runner_id = launch_runner(ec2, iam)
        print(f"  Runner: {runner_id}")

        # Wait for instance to pass status checks
        print("  Waiting for instance to pass status checks...")
        ec2.get_waiter("instance_status_ok").wait(InstanceIds=[runner_id])

        # Wait for SSM
        print("  Waiting for SSM agent...")
        wait_for_ssm(ssm, runner_id)
        print("  Runner ready")

        # Transfer test suite
        transfer_files(ssm, runner_id, tarball)

        # Build the test command
        # Pass AWS creds as env vars so _get_aws_boto3_client() doesn't need asyncpg/DB
        import os as _os
        aws_key = _os.environ.get("AWS_ACCESS_KEY_ID", "")
        aws_secret = _os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        aws_region = _os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        aws_env = (
            f"AWS_ACCESS_KEY_ID={aws_key} "
            f"AWS_SECRET_ACCESS_KEY={aws_secret} "
            f"AWS_DEFAULT_REGION={aws_region} "
        )
        # Build test command — email/password are optional for standalone phases
        _email_arg = f" --email {args.email}" if args.email else ""
        _password_arg = f" --password {args.password}" if args.password else ""
        test_cmd_parts = [
            "cd /tmp/nexplane_smoke",
            f"{aws_env}NEXPLANE_RUNNER_EC2=1 PYTHONPATH=/tmp/nexplane_smoke python3 smoke/test_aws_live.py"
            f" --base-url {args.base_url}"
            f"{_email_arg}"
            f"{_password_arg}"
            f" --phases {args.phases}"
            f" --backend-tailscale-ip {backend_ts_ip}",
        ]
        if args.tailscale_auth_key:
            test_cmd_parts[-1] += f" --tailscale-auth-key {args.tailscale_auth_key}"
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

        try:
            ssm_run(ssm, runner_id, test_script, timeout=7200)
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
