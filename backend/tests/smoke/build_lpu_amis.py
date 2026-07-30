#!/usr/bin/env python3
"""Build Ubuntu 20.04 and 22.04 smoke AMIs with Nexplane agent installed.

Stores AMI IDs in SSM:
  /nexplane/smoke-amis/ubuntu-20-agent/latest
  /nexplane/smoke-amis/ubuntu-22-agent/latest

Run on EC2: python3 build_lpu_amis.py
"""
import time
import sys
import requests
import boto3

PLATFORM_URL = "http://localhost:8000"
EMAIL = "admin@acme.example"
PASSWORD = "admin123"
REGION = "us-east-1"
SUBNET_ID = "subnet-0ae671c9d39da9a25"
SG_ID = "sg-08f614bff1e9aa1b1"
IAM_PROFILE = "NexplaneEC2TestProfile"
AGENT_SECRET = "sk-agent-2a9ea7a2367085c2399e4f7f41a2bb2c6fce6d3eaf4f1e7b"
CLOUD_ACCOUNT_ID = "ddc480c6-f7f9-4966-ac7f-8aa885d0210c"
NEXPLANE_URL_FROM_INSTANCE = "http://172.31.1.233:8000"  # platform private VPC IP

AMI_CONFIGS = [
    {
        "ami_id": "ami-0fb0b230890ccd1e6",
        "name": "smoke-agent-ubuntu20",
        "ssm_path": "/nexplane/smoke-amis/ubuntu-20-agent/latest",
    },
    {
        "ami_id": "ami-0446f93cefa2981e5",
        "name": "smoke-agent-ubuntu22",
        "ssm_path": "/nexplane/smoke-amis/ubuntu-22-agent/latest",
    },
]

USERDATA = """#!/bin/bash
set -e
apt-get update -qq
snap install amazon-ssm-agent --classic 2>/dev/null || true
systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null || true
systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null || true
"""


def log(msg):
    print(f"[build_lpu_amis] {msg}", flush=True)


def get_token():
    r = requests.post(f"{PLATFORM_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["access_token"]


def api(token, method, path, **kwargs):
    r = getattr(requests, method)(
        f"{PLATFORM_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )
    r.raise_for_status()
    return r.json()


def run_cr(token, change_type, desired_outcome, asset_ids=None, timeout=600):
    body = {"title": change_type, "change_type": change_type, "desired_outcome": desired_outcome}
    if asset_ids:
        body["target_asset_ids"] = asset_ids
    cr = api(token, "post", "/change-requests", json=body)
    cr_id = cr["id"]
    log(f"  CR {cr_id} created ({change_type})")
    for step in ("plan", "submit-for-approval"):
        api(token, "post", f"/change-requests/{cr_id}/{step}")
    api(token, "post", f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "ami-build"})
    api(token, "post", f"/change-requests/{cr_id}/execute")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = api(token, "get", f"/change-requests/{cr_id}")
        if cr["status"] == "completed":
            log(f"  CR {cr_id} completed")
            return cr
        if cr["status"] in ("failed", "rejected", "cancelled"):
            raise RuntimeError(f"CR {cr_id} failed: {cr.get('execution_result')}")
        time.sleep(15)
    raise TimeoutError(f"CR {cr_id} timed out")


def wait_ssm(ec2_id, timeout=300):
    ssm = boto3.client("ssm", region_name=REGION)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [ec2_id]}])
            if resp.get("InstanceInformationList"):
                log(f"  SSM ready: {ec2_id}")
                return
        except Exception:
            pass
        time.sleep(15)
    raise TimeoutError(f"SSM not ready for {ec2_id}")


def wait_platform_asset(ec2_id, private_ip, timeout=600):
    """Poll until asset with matching private IP registers with an agent_version."""
    hostname = f"ip-{private_ip.replace('.', '-')}.ec2.internal"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = get_token()
            # Search by hostname first
            assets = api(token, "get", "/assets", params={"q": hostname, "asset_type": "server", "limit": 50})
            if not isinstance(assets, list):
                assets = assets.get("items", [])
            for a in assets:
                meta = a.get("asset_metadata") or {}
                if (a.get("name") == hostname or private_ip in (meta.get("ip_addresses") or [])) and meta.get("agent_version"):
                    log(f"  Platform asset registered: {a['id']} ({hostname})")
                    return a["id"]
            # Fallback: scan all server assets by IP
            all_assets = api(token, "get", "/assets", params={"asset_type": "server", "limit": 200})
            if not isinstance(all_assets, list):
                all_assets = all_assets.get("items", [])
            for a in all_assets:
                meta = a.get("asset_metadata") or {}
                if private_ip in (meta.get("ip_addresses") or []) and meta.get("agent_version"):
                    log(f"  Platform asset registered (by IP): {a['id']}")
                    return a["id"]
        except Exception as e:
            log(f"  (waiting for asset: {e})")
        time.sleep(15)
    raise TimeoutError(f"Asset for {ec2_id} ({private_ip}) not registered")


def build_ami(config):
    log(f"\n=== Building AMI for {config['name']} ===")
    ec2 = boto3.client("ec2", region_name=REGION)
    ssm_client = boto3.client("ssm", region_name=REGION)

    resp = ec2.run_instances(
        ImageId=config["ami_id"],
        InstanceType="t3.micro",
        MinCount=1,
        MaxCount=1,
        SubnetId=SUBNET_ID,
        SecurityGroupIds=[SG_ID],
        IamInstanceProfile={"Name": IAM_PROFILE},
        UserData=USERDATA,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": config["name"]},
                {"Key": "nexplane-smoke-ami-build", "Value": "true"},
            ],
        }],
    )
    ec2_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched {ec2_id}, waiting for running state")
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[ec2_id],
        WaiterConfig={"Delay": 15, "MaxAttempts": 40},
    )
    private_ip = ec2.describe_instances(InstanceIds=[ec2_id])["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    log(f"  {ec2_id} running at {private_ip}")

    wait_ssm(ec2_id)

    token = get_token()
    log("  Deploying Nexplane agent via CR")
    run_cr(token, "deploy_nexplane_agent", {
        "instance_id": ec2_id,
        "nexplane_url": NEXPLANE_URL_FROM_INSTANCE,
        "nexplane_secret": AGENT_SECRET,
    }, asset_ids=[CLOUD_ACCOUNT_ID], timeout=300)

    log("  Waiting for agent to register on platform")
    wait_platform_asset(ec2_id, private_ip)

    # Verify the installed binary supports run_command before snapshotting
    log("  Verifying agent binary supports run_command")
    ssm_client = boto3.client("ssm", region_name=REGION)
    verify_resp = ssm_client.send_command(
        InstanceIds=[ec2_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["grep -c run_command /usr/local/bin/nexplane-agent"]},
    )
    verify_cmd_id = verify_resp["Command"]["CommandId"]
    deadline2 = time.time() + 60
    while time.time() < deadline2:
        time.sleep(10)
        try:
            inv = ssm_client.get_command_invocation(CommandId=verify_cmd_id, InstanceId=ec2_id)
            if inv["Status"] in ("Success", "Failed", "Cancelled"):
                count_str = inv.get("StandardOutputContent", "0").strip()
                count = int(count_str) if count_str.isdigit() else 0
                if count < 1:
                    raise RuntimeError(
                        f"Agent binary does not contain run_command (grep count={count_str!r}). "
                        f"Binary size: check S3 download. Re-run after verifying version file."
                    )
                log(f"  Agent binary verified: run_command present ({count} occurrences)")
                break
        except ssm_client.exceptions.InvocationDoesNotExist:
            pass

    log("  Creating AMI snapshot (instance will reboot)")
    ami_resp = ec2.create_image(
        InstanceId=ec2_id,
        Name=f"{config['name']}-{int(time.time())}",
        Description=f"Nexplane smoke AMI: {config['name']}",
        NoReboot=False,
    )
    new_ami_id = ami_resp["ImageId"]
    log(f"  AMI {new_ami_id} creating, waiting for available (~5-10 min)")
    ec2.get_waiter("image_available").wait(
        ImageIds=[new_ami_id],
        WaiterConfig={"Delay": 30, "MaxAttempts": 60},
    )
    log(f"  AMI {new_ami_id} available")

    ssm_client.put_parameter(Name=config["ssm_path"], Value=new_ami_id, Type="String", Overwrite=True)
    log(f"  Stored {new_ami_id} at SSM {config['ssm_path']}")

    ec2.terminate_instances(InstanceIds=[ec2_id])
    log(f"  Terminated build instance {ec2_id}")
    return new_ami_id


if __name__ == "__main__":
    for cfg in AMI_CONFIGS:
        try:
            ami = build_ami(cfg)
            print(f"SUCCESS: {cfg['ssm_path']} = {ami}")
        except Exception as e:
            print(f"FAILED: {cfg['name']}: {e}", file=sys.stderr)
            sys.exit(1)
    print("\nAll AMIs built successfully.")
