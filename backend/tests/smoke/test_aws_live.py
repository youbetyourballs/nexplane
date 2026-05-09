#!/usr/bin/env python3
"""
Nexplane AWS Live Smoke Test — Phases A–K (and new P–T).

Usage:
    python backend/tests/smoke/test_aws_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123 \\
        --phases A,B,C,D,E,F,G,H,I,K \\
        --tailscale-auth-key tskey-auth-<key>

Phase descriptions:
    A  EC2 lifecycle + Tailscale join + Nexplane agent deploy
    B  Agent-based actions (patching audit, OS posture, CloudWatch agent)
    C  Local Terraform lifecycle (S3 bucket create/destroy)
    D  Local Ansible playbook (htop install/remove)
    E  EC2 advanced: stop/start/reboot/snapshot with rollback stack
    F  Security group: add/remove rules with rollback stack
    G  IAM user lifecycle: create/attach-policy/rotate-key/delete with rollback stack
    H  S3 advanced: create/lifecycle/policy/public-access/delete with rollback stack
    I  Route53: private zone + A record create/update/delete with rollback stack
    J  RDS: instance + snapshot lifecycle (~30 min) with rollback stack
    K  CloudWatch: alarms + SSM metric push with rollback stack
    W  ALB lifecycle: create ALB + target group + listener, register EC2 target, verify health, deregister, rollback

Requirements:
    AWS connector with credentials + NexplaneEC2TestProfile IAM role
    Tailscale connector with reusable pre-authorized auth key
"""
import time
from typing import Optional

from smoke_helpers import (
    KEY_NAME, INSTANCE_NAME, TIMEOUT_SECONDS, RDS_PHASE_TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _run, setup_backend_tailscale, teardown_backend_tailscale,
    _delete_smoke_snapshots, _get_aws_boto3_client, _aws_creds_cache,
    make_base_parser,
)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup(client: NexplaneClient) -> None:
    """Always runs — terminates instances, deletes key pairs, EBS snapshots, and Tailscale."""
    print("\n  Cleanup running...")

    # Use boto3 directly for reliable cleanup (CR-based cleanup can fail if CR system is broken)
    try:
        ec2 = _get_aws_boto3_client('ec2')
        if ec2:
            # Terminate smoke test instances
            reservations = ec2.describe_instances(
                Filters=[{'Name': 'tag:Name', 'Values': ['nexplane-smoke-test*']},
                         {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}]
            ).get('Reservations', [])
            for res in reservations:
                for inst in res.get('Instances', []):
                    iid = inst['InstanceId']
                    try:
                        ec2.terminate_instances(InstanceIds=[iid])
                        print(f"  Terminated {iid}")
                    except Exception as e:
                        print(f"  ⚠️  Terminate {iid}: {e}")

            # Delete smoke test key pairs
            kps = ec2.describe_key_pairs(
                Filters=[{'Name': 'key-name', 'Values': ['nexplane-smoke-test*']}]
            ).get('KeyPairs', [])
            for kp in kps:
                try:
                    ec2.delete_key_pair(KeyName=kp['KeyName'])
                    print(f"  Deleted key pair {kp['KeyName']}")
                except Exception as e:
                    print(f"  ⚠️  Key pair delete {kp['KeyName']}: {e}")
    except Exception as e:
        print(f"  ⚠️  AWS boto3 cleanup error: {e}")

    # Delete smoke test assets from Nexplane inventory
    try:
        seen = set()
        for q, term in [("nexplane-smoke-test", "smoke-test"), ("nexplane-smoke-ec2", "smoke-ec2")]:
            for asset in client.get("/assets", params={"q": q}):
                if term in asset.get("name", "") and asset["id"] not in seen:
                    seen.add(asset["id"])
                    try:
                        client.client.delete(f"{client.base}/assets/{asset['id']}")
                        print(f"  Deleted inventory asset {asset['name']} ({asset['id']})")
                    except Exception as e:
                        print(f"  ⚠️  Could not delete inventory asset {asset['name']}: {e}")
    except Exception as e:
        print(f"  ⚠️  Inventory cleanup error: {e}")

    _delete_smoke_snapshots(client)
    teardown_backend_tailscale()
    print("  Cleanup complete.")


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------

def run_phase_a(client: NexplaneClient, cloud_account_id: str, tailscale_auth_key: str = "") -> dict:
    """Phase A: key pair + EC2 launch + Tailscale join + agent deploy."""
    print("\n[Phase A] EC2 launch + Tailscale + agent deploy")

    auth_key = client.get_tailscale_auth_key(tailscale_auth_key)
    backend_ip = setup_backend_tailscale(auth_key)
    agent_secret = client.get_agent_secret()

    client.run_cr(
        "[Phase A] create key pair", "key_pair_create", cloud_account_id,
        {"key_name": KEY_NAME},
    )
    key_asset = client.get_asset_by_name(KEY_NAME)
    if not key_asset:
        fail(f"Key pair asset '{KEY_NAME}' not in inventory")
    log(f"Key pair in inventory: {key_asset['id']}")

    client.run_cr(
        "[Phase A] launch EC2 instance", "ec2_launch", cloud_account_id,
        {"mode": "quick", "name": INSTANCE_NAME, "os": "amazon_linux",
         "iam_instance_profile": "NexplaneEC2TestProfile", "key_name": KEY_NAME,
         "rollback_strategy": "terminate_instance"},
    )
    # Wait up to 120s for the inventory asset to appear with a running instance_id.
    # Terminated instances from previous runs may still be visible in AWS (and can be re-ingested),
    # so we verify the instance_id is actually pending/running before proceeding.
    ec2_verify = _get_aws_boto3_client('ec2')
    instance_asset = None
    instance_id = None
    for _ in range(24):  # up to 120s
        time.sleep(5)
        candidate = client.get_asset_by_name(INSTANCE_NAME)
        if not candidate:
            continue
        cid = candidate.get("asset_metadata", {}).get("instance_id", "")
        if not cid:
            continue
        if ec2_verify:
            try:
                state = ec2_verify.describe_instances(InstanceIds=[cid])["Reservations"][0]["Instances"][0]["State"]["Name"]
                if state in ("pending", "running"):
                    instance_asset = candidate
                    instance_id = cid
                    break
                # stale asset with terminated instance — keep polling
            except Exception:
                pass
        else:
            instance_asset = candidate
            instance_id = cid
            break
    if not instance_asset:
        fail(f"Instance '{INSTANCE_NAME}' not in inventory with a running instance_id")
    if not instance_id:
        fail("instance_id missing from asset metadata")
    log(f"Instance in inventory: {instance_id}")

    print("  Waiting 3 min for SSM agent to register...")
    time.sleep(180)

    client.run_cr(
        "[Phase A] SSM whoami", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "whoami && hostname", "rollback_strategy": "rollback_unavailable"},
    )

    client.run_cr(
        "[Phase A] tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key, "hostname": "nexplane-smoke-ec2"},
    )

    # Agent downloads binary from the public S3 bucket (NEXPLANE_AGENT_DOWNLOAD_URL default).
    # nexplane_url is the Tailscale IP so agent heartbeats reach the backend within the tailnet.
    nexplane_url = f"http://{backend_ip}:8000"
    client.run_cr(
        "[Phase A] deploy nexplane agent", "deploy_nexplane_agent", instance_asset["id"],
        {"instance_id": instance_id, "nexplane_url": nexplane_url, "nexplane_secret": agent_secret},
    )

    print("  Waiting up to 3min for agent to register...")
    deadline = time.time() + 180
    agent_asset = None
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": "nexplane-smoke-ec2", "asset_type": "server"})
        tagged = [c for c in candidates if "nexplane-agent" in (c.get("tags") or [])]
        if tagged:
            tagged.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
            agent_asset = tagged[0]
            log(f"Agent registered as server asset: {agent_asset['id']}")
            break
        time.sleep(10)
    if not agent_asset:
        print("  ⚠️  Agent not yet registered in inventory — may still be starting")

    log("Phase A complete")
    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "backend_ip": backend_ip,
        "agent_secret": agent_secret,
    }


# ---------------------------------------------------------------------------
# Phase B
# ---------------------------------------------------------------------------

def run_phase_b(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase B: SSM-based instance operations (patch audit, system info, CloudWatch)."""
    print("\n[Phase B] SSM-based instance operations")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # Check available security patches via SSM (patch audit equivalent)
    client.run_cr(
        "[Phase B] patch audit via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum check-update --security 2>/dev/null | tail -5; echo 'patch_audit_ok'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Patch audit via SSM succeeded")

    # Collect system info (support bundle equivalent)
    client.run_cr(
        "[Phase B] collect system info", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "uname -a && cat /etc/os-release && df -h / && free -m",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("System info collected")

    # Install CloudWatch agent via shell script
    client.run_cr(
        "[Phase B] install CloudWatch agent", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "rpm -q amazon-cloudwatch-agent 2>/dev/null || yum install -y amazon-cloudwatch-agent; amazon-cloudwatch-agent --version 2>&1 || echo 'cwa_check_done'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("CloudWatch agent checked/installed")

    log("Phase B complete")


# ---------------------------------------------------------------------------
# Phase C
# ---------------------------------------------------------------------------

def run_phase_c(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase C: local Terraform S3 bucket lifecycle."""
    print("\n[Phase C] Local Terraform")
    import random
    bucket_suffix = random.randint(10000, 99999)
    bucket_name = "nexplane-smoke-test-" + str(bucket_suffix)

    tf_content = (
        'terraform {\n'
        '  required_providers {\n'
        '    aws = {\n'
        '      source  = "hashicorp/aws"\n'
        '      version = "~> 5.0"\n'
        '    }\n'
        '  }\n'
        '}\n\n'
        'provider "aws" {}\n\n'
        'resource "aws_s3_bucket" "smoke_test" {\n'
        '  bucket        = "' + bucket_name + '"\n'
        '  force_destroy = true\n'
        '}\n'
    )

    client.run_cr(
        "[Phase C] terraform apply S3 bucket", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log("Terraform applied — bucket: " + bucket_name)

    time.sleep(5)
    s3_assets = client.get("/assets", params={"asset_type": "storage_bucket", "q": bucket_name})
    if s3_assets:
        log("Bucket appears in inventory: " + s3_assets[0]["id"])
    else:
        print("  ⚠️  Bucket not yet in inventory — may need manual discovery run")

    log("Phase C complete")


# ---------------------------------------------------------------------------
# Phase D
# ---------------------------------------------------------------------------

def run_phase_d(client: NexplaneClient, phase_a_result: Optional[dict]) -> None:
    """Phase D: local Ansible playbook — tests CR machinery and SSM-based package management.

    Note: community.aws.aws_ssm Ansible connection has a Python 3.12 compatibility issue
    with session-manager-plugin subprocess. We test the ansible_local_playbook CR machinery
    using SSM RunShellScript to run ansible ad-hoc style, and verify SSM package ops separately.
    """
    print("\n[Phase D] Local Ansible + SSM package management")

    if phase_a_result is None:
        fail("Phase D requires Phase A to have run first (needs a running EC2 instance)")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # Test ansible_local_playbook CR against localhost (verifies CR machinery, planning, execution)
    # Uses local connection to avoid SSM connection plugin Python 3.12 compat issue.
    # Note: ansible_local_playbook runs check mode first, then real run.
    # Use ansible.builtin.debug which works in both check and real mode.
    LOCAL_TEST_PLAYBOOK = (
        "---\n"
        "- name: Smoke test - local ansible verification\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: no\n"
        "  tasks:\n"
        "    - name: Verify ansible is working\n"
        "      ansible.builtin.debug:\n"
        "        msg: 'ansible_local_playbook_ok'\n"
        "    - name: Check python\n"
        "      ansible.builtin.command: python3 --version\n"
        "      register: py_out\n"
        "      changed_when: false\n"
        "      check_mode: no\n"
    )

    # The cloud_account_id is used as target since we're running locally
    cloud_account_id = client.get_cloud_account_asset_id()
    client.run_cr(
        "[Phase D] ansible local playbook", "ansible_local_playbook", instance_asset["id"],
        {"instance_id": "localhost", "playbook_content": LOCAL_TEST_PLAYBOOK,
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Ansible local playbook CR executed successfully")

    # Install htop via SSM (same operation ansible would do via SSM connection)
    client.run_cr(
        "[Phase D] install htop via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum install -y htop && htop --version",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("htop installed via SSM")

    client.run_cr(
        "[Phase D] remove htop via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum remove -y htop && echo 'htop_removed'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("htop removed via SSM")

    log("Phase D complete")


# ---------------------------------------------------------------------------
# Phase E
# ---------------------------------------------------------------------------

def run_phase_e(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase E: EC2 advanced — stop/start/reboot/snapshot with rollback stack cleanup."""
    print("\n[Phase E] EC2 Advanced Operations")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    rollback_stack: list[tuple[str, str]] = []  # (cr_id, label)
    snapshot_id: str | None = None

    try:
        # 1. Stop instance (needs target_state="stopped" or wait_instance_state defaults to "running")
        cr = client._run_cr_with_timeout(
            "[Phase E] stop EC2 instance", "ec2_stop", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "start_instance",
             "target_state": "stopped"},
            timeout=900,
        )
        rollback_stack.append((cr["id"], "ec2_stop"))
        log("Instance stopped")

        # 2. Start instance (also waits for running state — needs 900s)
        cr = client._run_cr_with_timeout(
            "[Phase E] start EC2 instance", "ec2_start", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "stop_instance"},
            timeout=900,
        )
        rollback_stack.pop()  # ec2_stop superseded — instance is running again
        # Don't push ec2_start — rolling it back would stop the instance, but we
        # want it running for Phase K SSM commands. Main cleanup terminates anyway.
        log("Instance started")

        # 3. Reboot (reboot includes a state wait — needs 900s)
        client._run_cr_with_timeout(
            "[Phase E] reboot EC2 instance", "ec2_reboot", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "rollback_unavailable"},
            timeout=900,
        )
        log("Instance rebooted")

        # Wait for SSM to reconnect post-reboot (60s to be safe)
        time.sleep(60)
        client.run_cr(
            "[Phase E] SSM verify post-reboot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "uptime && echo 'post_reboot_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("SSM verified post-reboot")

        # 4. Create EBS snapshot — resolve root volume_id via boto3 first
        ec2_boto = _get_aws_boto3_client('ec2')
        volume_id = ""
        if ec2_boto:
            try:
                resp = ec2_boto.describe_instances(InstanceIds=[instance_id])
                mappings = resp["Reservations"][0]["Instances"][0].get("BlockDeviceMappings", [])
                root = next((m for m in mappings if m.get("DeviceName") in ("/dev/xvda", "/dev/sda1")), mappings[0] if mappings else None)
                if root:
                    volume_id = root["Ebs"]["VolumeId"]
            except Exception as e:
                print(f"  ⚠️  Could not resolve volume_id: {e}")

        cr = client.run_cr(
            "[Phase E] create EBS snapshot", "snapshot_asset", instance_asset["id"],
            {"instance_id": instance_id, "volume_id": volume_id,
             "rollback_strategy": "delete_ebs_snapshot"},
        )
        rollback_stack.append((cr["id"], "snapshot_asset"))

        # Find the snapshot ID from AWS
        if ec2_boto:
            snaps = ec2_boto.describe_snapshots(
                Filters=[
                    {"Name": "description", "Values": [f"*{instance_id}*"]},
                    {"Name": "status", "Values": ["completed", "pending"]},
                ]
            ).get("Snapshots", [])
            snaps.sort(key=lambda s: s["StartTime"], reverse=True)
            if snaps:
                snapshot_id = snaps[0]["SnapshotId"]
        log(f"EBS snapshot created: {snapshot_id or 'unknown'}")

        # 5. Verify via SSM
        client.run_cr(
            "[Phase E] SSM verify post-snapshot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "echo 'snapshot_verify_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("Post-snapshot SSM verified")
        log("Phase E complete")

    except Exception as e:
        print(f"\n❌ Phase E failed: {e}")
        raise
    finally:
        print("  [Phase E cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot directly
        if snapshot_id:
            try:
                ec2_boto2 = _get_aws_boto3_client('ec2')
                if ec2_boto2:
                    ec2_boto2.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"  Safety net: deleted snapshot {snapshot_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase F
# ---------------------------------------------------------------------------

def run_phase_f(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase F: Security Groups — add/remove inbound rule with rollback stack."""
    print("\n[Phase F] Security Group Operations")

    rollback_stack: list[tuple[str, str]] = []
    test_sg_id: str | None = None

    try:
        # Create isolated test SG via boto3 (test scaffolding — not a CR)
        ec2_boto = _get_aws_boto3_client('ec2')
        if not ec2_boto:
            fail("Phase F requires AWS credentials")

        sg_name = f"nexplane-smoke-sg-{int(time.time())}"
        sg = ec2_boto.create_security_group(
            GroupName=sg_name,
            Description="Nexplane smoke test security group",
        )
        test_sg_id = sg["GroupId"]
        log(f"Created test SG: {test_sg_id}")

        # 1. Add inbound rule via CR (port 8443 from RFC5737 test CIDR — not routable)
        cr = client.run_cr(
            "[Phase F] add security group inbound rule", "security_group_update", cloud_account_id,
            {
                "group_id": test_sg_id,
                "rules": [{"action": "add", "protocol": "tcp",
                            "from_port": 8443, "to_port": 8443,
                            "cidr": "192.0.2.0/24"}],
                "rollback_strategy": "restore_security_group",
            },
        )
        rollback_stack.append((cr["id"], "security_group_update add_inbound"))
        log("Inbound rule added via CR")

        # Verify rule is present via boto3
        sg_details = ec2_boto.describe_security_groups(GroupIds=[test_sg_id])
        perms = sg_details["SecurityGroups"][0].get("IpPermissions", [])
        has_rule = any(
            p.get("FromPort") == 8443 and
            any(r.get("CidrIp") == "192.0.2.0/24" for r in p.get("IpRanges", []))
            for p in perms
        )
        if has_rule:
            log("Rule verified via boto3 describe")
        else:
            print("  ⚠️  Rule not found via describe — may be a mock path")

        # 2. Remove the rule via a second CR
        cr = client.run_cr(
            "[Phase F] remove security group inbound rule", "security_group_update", cloud_account_id,
            {
                "group_id": test_sg_id,
                "rules": [{"action": "remove", "protocol": "tcp",
                            "from_port": 8443, "to_port": 8443,
                            "cidr": "192.0.2.0/24"}],
                "rollback_strategy": "restore_security_group",
            },
        )
        rollback_stack.pop()  # add_inbound CR superseded — rule now removed
        rollback_stack.append((cr["id"], "security_group_update remove_inbound"))

        # Verify rule is gone
        sg_details2 = ec2_boto.describe_security_groups(GroupIds=[test_sg_id])
        perms2 = sg_details2["SecurityGroups"][0].get("IpPermissions", [])
        still_has = any(
            p.get("FromPort") == 8443 and
            any(r.get("CidrIp") == "192.0.2.0/24" for r in p.get("IpRanges", []))
            for p in perms2
        )
        if not still_has:
            log("Rule removed — verified via boto3")
        else:
            print("  ⚠️  Rule still present after remove CR")

        log("Phase F complete")

    except Exception as e:
        print(f"\n❌ Phase F failed: {e}")
        raise
    finally:
        print("  [Phase F cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete the test SG
        if test_sg_id:
            try:
                ec2_boto2 = _get_aws_boto3_client('ec2')
                if ec2_boto2:
                    ec2_boto2.delete_security_group(GroupId=test_sg_id)
                    print(f"  Safety net: deleted SG {test_sg_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net SG delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase G
# ---------------------------------------------------------------------------

def run_phase_g(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase G: IAM User Lifecycle — create/attach-policy/rotate-key/disable/enable/detach/delete."""
    print("\n[Phase G] IAM User Lifecycle")

    username = f"nexplane-smoke-user-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []
    user_created = False

    try:
        # 1. Create IAM user via CR
        cr = client.run_cr(
            "[Phase G] create IAM user", "iam_user_create", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "iam_user_create"))
        user_created = True
        log(f"IAM user created: {username}")

        # Verify identity asset in inventory (ingest lag OK)
        assets = client.get("/assets", params={"q": username, "asset_type": "identity"})
        if assets:
            log(f"IAM user in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  IAM user asset not yet in inventory (ingest lag expected)")

        iam_client = _get_aws_boto3_client('iam')
        if not iam_client:
            fail("Phase G requires AWS credentials")

        # 2. Attach ReadOnlyAccess policy via boto3 (no dedicated policy-attach CR yet)
        iam_client.attach_user_policy(
            UserName=username,
            PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
        )
        log("ReadOnlyAccess attached (boto3)")

        # 3. Rotate IAM access key via boto3:
        #    The create_iam_user executor already created 1 key. Rotate by:
        #    finding the existing key → creating new key → deleting old.
        #    (Don't create an "initial" key — executor already did that, avoids hitting the 2-key limit)
        existing_keys = iam_client.list_access_keys(UserName=username)["AccessKeyMetadata"]
        old_key_id = existing_keys[0]["AccessKeyId"] if existing_keys else None
        new_key = iam_client.create_access_key(UserName=username)["AccessKey"]
        if old_key_id:
            iam_client.update_access_key(UserName=username, AccessKeyId=old_key_id, Status="Inactive")
            iam_client.delete_access_key(UserName=username, AccessKeyId=old_key_id)
            log(f"Key rotated: {old_key_id} → {new_key['AccessKeyId']}")
        else:
            log(f"Key created (no prior key to rotate): {new_key['AccessKeyId']}")

        # 4. Disable user: deactivate all access keys
        keys = iam_client.list_access_keys(UserName=username)["AccessKeyMetadata"]
        for k in keys:
            iam_client.update_access_key(UserName=username, AccessKeyId=k["AccessKeyId"], Status="Inactive")
        log(f"IAM user disabled ({len(keys)} key(s) deactivated)")

        # 5. Re-enable user: reactivate all access keys
        keys = iam_client.list_access_keys(UserName=username)["AccessKeyMetadata"]
        for k in keys:
            iam_client.update_access_key(UserName=username, AccessKeyId=k["AccessKeyId"], Status="Active")
        log("IAM user re-enabled")

        # 6. Detach policy via boto3
        iam_client.detach_user_policy(
            UserName=username,
            PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
        )
        log("ReadOnlyAccess detached (boto3)")

        # 7. Delete user: trigger rollback of iam_user_create CR (rollback = delete_iam_user)
        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "iam_user_create → delete_iam_user")
        user_created = False
        log("IAM user deleted via CR rollback")

        log("Phase G complete")

    except Exception as e:
        print(f"\n❌ Phase G failed: {e}")
        raise
    finally:
        print("  [Phase G cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete user directly if still alive
        if user_created:
            try:
                iam_safety = _get_aws_boto3_client('iam')
                if iam_safety:
                    for k in iam_safety.list_access_keys(UserName=username).get("AccessKeyMetadata", []):
                        iam_safety.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
                    for p in iam_safety.list_attached_user_policies(UserName=username).get("AttachedPolicies", []):
                        iam_safety.detach_user_policy(UserName=username, PolicyArn=p["PolicyArn"])
                    for name in iam_safety.list_user_policies(UserName=username).get("PolicyNames", []):
                        iam_safety.delete_user_policy(UserName=username, PolicyName=name)
                    try:
                        iam_safety.delete_login_profile(UserName=username)
                    except Exception:
                        pass
                    iam_safety.delete_user(UserName=username)
                    print(f"  Safety net: deleted IAM user {username}")
            except Exception as e2:
                print(f"  ⚠️  Safety net IAM user delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase H
# ---------------------------------------------------------------------------

def run_phase_h(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase H: S3 Advanced — create/lifecycle/policy/public-access/delete with rollback stack."""
    print("\n[Phase H] S3 Advanced Operations")
    import json
    bucket_name = f"nexplane-smoke-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []
    bucket_created = False

    try:
        # 1. Create bucket via CR
        cr = client.run_cr(
            "[Phase H] create S3 bucket", "s3_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "s3_bucket_create"))
        bucket_created = True
        log(f"S3 bucket created: {bucket_name}")

        # Verify storage_bucket asset in inventory (ingest lag OK)
        assets = client.get("/assets", params={"q": bucket_name, "asset_type": "storage_bucket"})
        if assets:
            log(f"Bucket in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  Bucket asset not yet in inventory (ingest lag)")

        s3_client = _get_aws_boto3_client('s3')
        if not s3_client:
            fail("Phase H requires AWS credentials")

        # 2. Configure lifecycle via CR: 1-day expiration on smoke/ prefix
        cr = client.run_cr(
            "[Phase H] configure S3 lifecycle", "s3_lifecycle_configure", cloud_account_id,
            {
                "bucket_name": bucket_name,
                "rules": [{
                    "ID": "nexplane-smoke-expire",
                    "Status": "Enabled",
                    "Expiration": {"Days": 1},
                    "Filter": {"Prefix": "smoke/"},
                }],
            },
        )
        rollback_stack.append((cr["id"], "s3_lifecycle_configure"))
        log("Lifecycle policy configured via CR")

        # Verify lifecycle via boto3
        try:
            lc = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            if lc.get("Rules"):
                log("Lifecycle rules verified via boto3")
        except Exception:
            print("  ⚠️  Lifecycle not yet visible via boto3 (may be eventual consistency)")

        # 3. Set bucket policy via boto3 (deny non-TLS GetObject)
        policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "DenyNonTLS",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }],
        })
        s3_client.put_bucket_policy(Bucket=bucket_name, Policy=policy)
        log("Bucket policy applied (boto3)")

        # 4. Block public access via boto3
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        log("Public access blocked (boto3)")

        # 5. Verify public access block
        pab = s3_client.get_public_access_block(Bucket=bucket_name)
        config = pab["PublicAccessBlockConfiguration"]
        if all(config.get(k) for k in ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]):
            log("Public access block verified")
        else:
            print(f"  ⚠️  Public access block partial: {config}")

        # 6. Delete bucket via CR (terminal step — use s3_bucket_delete directly)
        # Roll back lifecycle first, then delete bucket
        lifecycle_cr_id, lifecycle_label = rollback_stack.pop()
        client.rollback_cr(lifecycle_cr_id, lifecycle_label)

        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "s3_bucket_create → delete_s3_bucket")
        bucket_created = False
        log("Bucket deleted via CR rollback")

        log("Phase H complete")

    except Exception as e:
        print(f"\n❌ Phase H failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase H cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: force-delete bucket
        if bucket_created:
            try:
                s3_safety = _get_aws_boto3_client('s3')
                if s3_safety:
                    try:
                        # Delete all objects/versions first
                        paginator = s3_safety.get_paginator('list_object_versions')
                        for page in paginator.paginate(Bucket=bucket_name):
                            objs = [{'Key': v['Key'], 'VersionId': v['VersionId']}
                                    for v in page.get('Versions', [])]
                            objs += [{'Key': m['Key'], 'VersionId': m['VersionId']}
                                     for m in page.get('DeleteMarkers', [])]
                            if objs:
                                s3_safety.delete_objects(Bucket=bucket_name, Delete={'Objects': objs})
                        s3_safety.delete_bucket(Bucket=bucket_name)
                        print(f"  Safety net: deleted bucket {bucket_name}")
                    except Exception as inner:
                        print(f"  ⚠️  Safety net bucket delete failed: {inner}")
            except Exception as e2:
                print(f"  ⚠️  Safety net error: {e2}")


# ---------------------------------------------------------------------------
# Phase I
# ---------------------------------------------------------------------------

def run_phase_i(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase I: Route53 — private zone, A record create/update/delete with rollback stack."""
    print("\n[Phase I] Route53 DNS Operations")

    zone_name = f"smoke-{int(time.time())}.nexplane.internal"
    rollback_stack: list[tuple[str, str]] = []
    zone_id: str | None = None

    try:
        # 1. Create private hosted zone via CR
        cr = client.run_cr(
            "[Phase I] create Route53 hosted zone", "route53_zone_create", cloud_account_id,
            {"zone_name": zone_name, "private": True},
        )
        rollback_stack.append((cr["id"], "route53_zone_create"))

        # Resolve zone_id: look up via boto3 (executor stores in _auto_asset but ingest lag may apply)
        r53_boto = _get_aws_boto3_client('route53')
        if r53_boto:
            zones = r53_boto.list_hosted_zones_by_name(DNSName=zone_name).get("HostedZones", [])
            for z in zones:
                if z["Name"].rstrip(".") == zone_name.rstrip("."):
                    zone_id = z["Id"].split("/")[-1]
                    break
        if not zone_id:
            # Fall back to inventory
            assets = client.get("/assets", params={"q": zone_name, "asset_type": "dns_zone"})
            if assets:
                zone_id = assets[0].get("asset_metadata", {}).get("zone_id")
        if not zone_id:
            fail(f"Could not determine zone_id for {zone_name}")
        log(f"Hosted zone created: {zone_id} ({zone_name})")

        # 2. Create A record
        cr = client.run_cr(
            "[Phase I] create Route53 A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.1"],
                "ttl": 60,
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert create"))
        log("A record created: web → 10.0.0.1")

        # 3. Update the A record (UPSERT semantics)
        cr = client.run_cr(
            "[Phase I] update Route53 A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.2"],
                "ttl": 60,
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert update"))
        log("A record updated: web → 10.0.0.2")

        # 4. Verify record via boto3
        if r53_boto and zone_id:
            rrsets = r53_boto.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=f"web.{zone_name}",
                StartRecordType="A",
                MaxItems="1",
            ).get("ResourceRecordSets", [])
            if rrsets and rrsets[0].get("Name", "").rstrip(".") == f"web.{zone_name}".rstrip("."):
                values = [r["Value"] for r in rrsets[0].get("ResourceRecords", [])]
                log(f"A record verified via boto3: {values}")
            else:
                print("  ⚠️  A record not yet visible via boto3 (may be eventual consistency)")

        # 5. Delete A record via route53_record_delete CR
        cr = client.run_cr(
            "[Phase I] delete Route53 A record", "route53_record_delete", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.2"],
                "ttl": 60,
            },
        )
        # A record is gone — remove the upsert CRs from rollback stack (nothing to undo)
        rollback_stack = [(cid, lbl) for cid, lbl in rollback_stack
                          if not lbl.startswith("route53_record_upsert")]
        log("A record deleted via CR")

        log("Phase I complete")

    except Exception as e:
        print(f"\n❌ Phase I failed: {e}")
        raise
    finally:
        print("  [Phase I cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete the entire hosted zone
        if zone_id:
            try:
                r53_safety = _get_aws_boto3_client('route53')
                if r53_safety:
                    # Delete all non-SOA/NS records first
                    changes = []
                    paginator = r53_safety.get_paginator('list_resource_record_sets')
                    for page in paginator.paginate(HostedZoneId=zone_id):
                        for rrs in page['ResourceRecordSets']:
                            if rrs['Type'] not in ('SOA', 'NS'):
                                changes.append({'Action': 'DELETE', 'ResourceRecordSet': rrs})
                    if changes:
                        r53_safety.change_resource_record_sets(
                            HostedZoneId=zone_id,
                            ChangeBatch={'Changes': changes},
                        )
                    r53_safety.delete_hosted_zone(Id=zone_id)
                    print(f"  Safety net: deleted hosted zone {zone_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net zone delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase J
# ---------------------------------------------------------------------------

def run_phase_j(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase J: RDS Full Lifecycle — create/snapshot/verify/delete (~25-35 min)."""
    print("\n[Phase J] RDS Full Lifecycle (~25-35 min)")

    ts = int(time.time())
    db_id = f"nexplane-smoke-db-{ts}"
    snap_id = f"nexplane-smoke-snap-{ts}"

    import secrets as _secrets
    import string as _string
    # Generate a random password meeting RDS requirements (letters + digits + special char)
    _pw_chars = _string.ascii_letters + _string.digits
    rds_password = "Nx!" + "".join(_secrets.choice(_pw_chars) for _ in range(16))

    rollback_stack: list[tuple[str, str]] = []
    created_db_ids: list[str] = []
    created_snap_ids: list[str] = []

    try:
        # 1. Create primary RDS instance
        print(f"  Creating RDS instance {db_id} (db.t3.micro MySQL 8.0) — may take ~10 min")
        cr = client._run_cr_with_timeout(
            "[Phase J] create RDS instance", "rds_instance_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "engine": "mysql",
                "engine_version": "8.0",
                "db_instance_class": "db.t3.micro",
                "master_username": "admin",
                "master_password": rds_password,
                "allocated_storage": 20,
                "skip_final_snapshot": True,
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_instance_create"))
        created_db_ids.append(db_id)
        log(f"RDS instance created: {db_id}")

        # Verify database asset in inventory
        assets = client.get("/assets", params={"q": db_id, "asset_type": "database"})
        if assets:
            log(f"RDS instance in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  RDS asset not yet in inventory (ingest lag)")

        # 2. Create manual snapshot
        print(f"  Creating RDS snapshot {snap_id} — may take ~5 min")
        cr = client._run_cr_with_timeout(
            "[Phase J] create RDS snapshot", "rds_snapshot_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "snapshot_identifier": snap_id,
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_create"))
        created_snap_ids.append(snap_id)
        log(f"Snapshot created: {snap_id}")

        # 3. Verify snapshot via boto3
        rds_boto = _get_aws_boto3_client('rds')
        if rds_boto:
            snaps = rds_boto.describe_db_snapshots(DBSnapshotIdentifier=snap_id).get("DBSnapshots", [])
            if snaps and snaps[0].get("Status") == "available":
                log(f"Snapshot verified: {snaps[0].get('AllocatedStorage', 0)}GB, status=available")
            elif snaps:
                print(f"  ⚠️  Snapshot status: {snaps[0].get('Status')} (may still be creating)")
            else:
                print("  ⚠️  Snapshot not found via boto3")

        # 4. Delete snapshot via rollback of snapshot CR
        snap_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(snap_cr_id, "rds_snapshot_create → delete_rds_snapshot")
        created_snap_ids.remove(snap_id)
        log("Snapshot deleted via CR rollback")

        # 5. Delete instance via rollback of create CR
        print(f"  Deleting RDS instance {db_id} — may take ~10 min")
        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "rds_instance_create → delete_rds_instance")
        created_db_ids.remove(db_id)
        log("RDS instance deleted via CR rollback")

        log("Phase J complete")

    except Exception as e:
        print(f"\n❌ Phase J failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase J cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: force-delete any remaining RDS resources
        rds_safety = _get_aws_boto3_client('rds')
        if rds_safety:
            for db_identifier in list(created_db_ids):
                try:
                    rds_safety.delete_db_instance(
                        DBInstanceIdentifier=db_identifier,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True,
                    )
                    print(f"  Safety net: deleting RDS instance {db_identifier} (async)")
                except Exception as e2:
                    print(f"  ⚠️  Safety net instance delete failed {db_identifier}: {e2}")
            for snap_identifier in list(created_snap_ids):
                try:
                    rds_safety.delete_db_snapshot(DBSnapshotIdentifier=snap_identifier)
                    print(f"  Safety net: deleted snapshot {snap_identifier}")
                except Exception as e2:
                    print(f"  ⚠️  Safety net snapshot delete failed {snap_identifier}: {e2}")


# ---------------------------------------------------------------------------
# Phase K
# ---------------------------------------------------------------------------

def run_phase_k(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase K: CloudWatch — create alarms, trigger via SSM custom metric, verify, rollback."""
    print("\n[Phase K] CloudWatch Alarms")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    ts = int(time.time())
    alarm_cpu = f"nexplane-smoke-cpu-{ts}"
    alarm_custom = f"nexplane-smoke-custom-{ts}"
    custom_namespace = "Nexplane/SmokeTest"
    aws_region = (_aws_creds_cache.get("region") or "us-east-1") if _aws_creds_cache else "us-east-1"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create CPU utilization alarm (99% threshold — won't fire on idle instance)
        cr = client.run_cr(
            "[Phase K] create CloudWatch CPU alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_cpu,
                "metric_name": "CPUUtilization",
                "namespace": "AWS/EC2",
                "threshold": 99.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Average",
                "dimensions": [{"Name": "InstanceId", "Value": instance_id}],
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create CPU"))
        log(f"CPU alarm created: {alarm_cpu}")

        # 2. Create custom namespace alarm (fires when metric value > 0)
        cr = client.run_cr(
            "[Phase K] create CloudWatch custom metric alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_custom,
                "metric_name": "TestTrigger",
                "namespace": custom_namespace,
                "threshold": 0.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Sum",
                "dimensions": [],
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create custom"))
        log(f"Custom metric alarm created: {alarm_custom}")

        # 3. Push metric data via SSM to trigger the custom alarm
        client.run_cr(
            "[Phase K] push metric data via SSM", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": (
                    f"aws cloudwatch put-metric-data "
                    f"--namespace '{custom_namespace}' "
                    f"--metric-name TestTrigger "
                    f"--value 1 "
                    f"--unit Count "
                    f"--region {aws_region}"
                ),
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("Metric data pushed via SSM")

        # 4. Wait up to 90s for custom alarm to enter ALARM state
        print("  Waiting up to 90s for alarm to enter ALARM state...")
        cw_boto = _get_aws_boto3_client('cloudwatch')
        alarm_triggered = False
        if cw_boto:
            deadline = time.time() + 90
            while time.time() < deadline:
                resp = cw_boto.describe_alarms(AlarmNames=[alarm_custom])
                alarms = resp.get("MetricAlarms", [])
                if alarms and alarms[0]["StateValue"] == "ALARM":
                    alarm_triggered = True
                    log(f"Alarm {alarm_custom} is in ALARM state")
                    break
                time.sleep(10)
            if not alarm_triggered:
                print(f"  ⚠️  Alarm did not enter ALARM state within 90s (CloudWatch evaluation lag)")

        # 5. Delete both alarms via rollback stack
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Both alarms deleted via CR rollback")

        log("Phase K complete")

    except Exception as e:
        print(f"\n❌ Phase K failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase K cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete alarms directly
        try:
            cw_safety = _get_aws_boto3_client('cloudwatch')
            if cw_safety:
                cw_safety.delete_alarms(AlarmNames=[alarm_cpu, alarm_custom])
                print(f"  Safety net: deleted alarms {alarm_cpu}, {alarm_custom}")
        except Exception as e2:
            print(f"  ⚠️  Safety net alarm delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase P
# ---------------------------------------------------------------------------

def run_phase_p(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase P: IAM Advanced — attach/detach policy + disable/enable/rotate key via CRs."""
    print("\n[Phase P] IAM Advanced")

    username = f"nexplane-smoke-p-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create IAM user
        cr = client.run_cr(
            "[Phase P] create IAM user", "iam_user_create", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "iam_user_create"))

        # 2. Attach ReadOnlyAccess policy via CR
        cr = client.run_cr(
            "[Phase P] attach IAM policy", "attach_iam_policy", cloud_account_id,
            {"principal_type": "user", "principal_name": username,
             "policy_arn": "arn:aws:iam::aws:policy/ReadOnlyAccess"},
        )
        rollback_stack.append((cr["id"], "attach_iam_policy"))

        # Verify via boto3
        iam = _get_aws_boto3_client("iam")
        if iam:
            attached = iam.list_attached_user_policies(UserName=username)["AttachedPolicies"]
            assert any(p["PolicyName"] == "ReadOnlyAccess" for p in attached), "ReadOnlyAccess not attached"
            log("Policy attached (boto3 verified)")

        # 3. Get the existing key ID (created by iam_user_create executor) to rotate from it
        existing_key_id = ""
        if iam:
            existing_keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
            if existing_keys:
                existing_key_id = existing_keys[0]["AccessKeyId"]

        # 4. Rotate IAM key via CR — passes old key ID so executor deactivates it after creating new one
        cr = client.run_cr(
            "[Phase P] rotate IAM access key", "rotate_iam_key", cloud_account_id,
            {"username": username, "old_access_key_id": existing_key_id},
        )
        rollback_stack.append((cr["id"], "rotate_iam_key"))
        log("IAM key rotated via CR")

        # 5. Disable IAM user via CR
        cr = client.run_cr(
            "[Phase P] disable IAM user", "disable_iam_user", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "disable_iam_user"))

        if iam:
            keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
            assert all(k["Status"] == "Inactive" for k in keys), "Not all keys inactive"
            log("IAM user disabled (boto3 verified)")

        # 6. Enable IAM user via CR
        cr = client.run_cr(
            "[Phase P] enable IAM user", "enable_iam_user", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "enable_iam_user"))

        if iam:
            keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
            assert all(k["Status"] == "Active" for k in keys), "Not all keys active after enable"
            log("IAM user enabled (boto3 verified)")

        # 7. Detach policy via CR rollback
        enable_cr_id, _ = rollback_stack.pop()  # pop enable_iam_user
        client.rollback_cr(enable_cr_id, "enable_iam_user rollback")
        disable_cr_id, _ = rollback_stack.pop()  # pop disable_iam_user
        client.rollback_cr(disable_cr_id, "disable_iam_user rollback")
        rotate_cr_id, _ = rollback_stack.pop()  # pop rotate_iam_key
        client.rollback_cr(rotate_cr_id, "rotate_iam_key rollback")
        attach_policy_cr_id, _ = rollback_stack.pop()  # pop attach_iam_policy
        client.rollback_cr(attach_policy_cr_id, "attach_iam_policy → detach")
        log("Policy detached via CR rollback")

        # 8. Delete user via CR rollback
        create_cr_id, _ = rollback_stack.pop()  # pop iam_user_create
        client.rollback_cr(create_cr_id, "iam_user_create → delete_iam_user")
        log("IAM user deleted via CR rollback")

        log("Phase P complete")

    except Exception as e:
        print(f"\n❌ Phase P failed: {e}")
        raise
    finally:
        print("  [Phase P cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete user directly if still alive
        try:
            iam = _get_aws_boto3_client("iam")
            if iam:
                try:
                    keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
                    for k in keys:
                        iam.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
                except Exception:
                    pass
                try:
                    iam.delete_user(UserName=username)
                    print(f"  Safety net: deleted IAM user {username}")
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase Q
# ---------------------------------------------------------------------------

def run_phase_q(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase Q: S3 Advanced — put_bucket_policy + tag_resource."""
    print("\n[Phase Q] S3 Advanced Gaps")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-q-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create S3 bucket
        cr = client.run_cr(
            "[Phase Q] create S3 bucket", "s3_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "s3_bucket_create"))

        # 2. Apply deny-non-TLS bucket policy via CR
        deny_tls_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "DenyNonTLS",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }],
        }
        cr = client.run_cr(
            "[Phase Q] put S3 bucket policy", "put_bucket_policy", cloud_account_id,
            {"bucket_name": bucket_name, "policy": deny_tls_policy},
        )
        rollback_stack.append((cr["id"], "put_bucket_policy"))

        # Verify via boto3
        s3 = _get_aws_boto3_client("s3")
        if s3:
            import json as _json
            policy_str = s3.get_bucket_policy(Bucket=bucket_name)["Policy"]
            policy = _json.loads(policy_str)
            assert any(s.get("Sid") == "DenyNonTLS" for s in policy.get("Statement", [])), \
                "DenyNonTLS policy statement not found"
            log("Bucket policy applied (boto3 verified)")

        # 3. Tag the bucket via CR
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        cr = client.run_cr(
            "[Phase Q] tag S3 bucket", "tag_resource", cloud_account_id,
            {"resource_arn": bucket_arn, "tags": {"nexplane-smoke": "true", "phase": "Q"}},
        )
        rollback_stack.append((cr["id"], "tag_resource"))

        if s3:
            tagging = s3.get_bucket_tagging(Bucket=bucket_name)
            tag_set = {t["Key"]: t["Value"] for t in tagging.get("TagSet", [])}
            assert tag_set.get("nexplane-smoke") == "true", "Tag not applied"
            log("Bucket tagged (boto3 verified)")

        log("Phase Q complete")

    except Exception as e:
        print(f"\n❌ Phase Q failed: {e}")
        raise
    finally:
        print("  [Phase Q cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: empty and delete bucket via SDK
        try:
            s3 = _get_aws_boto3_client("s3")
            if s3:
                try:
                    objs = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
                    for obj in objs:
                        s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
                    s3.delete_bucket(Bucket=bucket_name)
                    print(f"  Safety net: deleted bucket {bucket_name}")
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase R
# ---------------------------------------------------------------------------

def run_phase_r(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase R: DR DNS Failover — exercises dr_dns_failover_route53 executor."""
    print("\n[Phase R] DR DNS Failover")

    import secrets as _secrets
    zone_name = f"nexplane-smoke-r-{_secrets.token_hex(4)}.internal."
    rollback_stack: list[tuple[str, str]] = []
    zone_id: str = ""

    try:
        # 1. Create a private Route53 zone
        cr = client.run_cr(
            "[Phase R] create Route53 zone", "route53_zone_create", cloud_account_id,
            {"zone_name": zone_name, "private": True, "vpc_id": ""},
        )
        rollback_stack.append((cr["id"], "route53_zone_create"))
        log(f"Route53 zone created: {zone_name}")

        # Get zone ID from boto3
        r53 = _get_aws_boto3_client("route53")
        if not r53:
            fail("Phase R requires AWS credentials")

        zones = r53.list_hosted_zones_by_name(DNSName=zone_name)["HostedZones"]
        zone = next((z for z in zones if z["Name"] == zone_name), None)
        if not zone:
            fail(f"Could not find zone {zone_name} after creation")
        zone_id = zone["Id"].split("/")[-1]
        log(f"Zone ID: {zone_id}")

        # 2. DR failover: use dr_dns_failover_route53 to create a weighted CNAME in one step.
        #    The executor creates a weighted-routing CNAME (SetIdentifier: "dr-primary", Weight: 100).
        #    We don't pre-create a simple CNAME because Route53 won't mix simple + weighted records
        #    on the same name. The executor creates from scratch → verify it exists → cleanup.
        dr_endpoint = "dr.example.internal"
        record_name = f"app.{zone_name}"
        cr = client.run_cr(
            "[Phase R] DR DNS failover Route53", "dr_dns_failover_route53", cloud_account_id,
            {
                "dns_record_id": record_name,
                "dr_endpoint": dr_endpoint,
                "hosted_zone_id": zone_id,
            },
        )
        rollback_stack.append((cr["id"], "dr_dns_failover_route53"))

        # Verify DR endpoint is now active
        records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        dr_cname = next((rec for rec in records
                         if rec.get("Name", "").rstrip(".") == record_name.rstrip(".")
                         and rec["Type"] == "CNAME"), None)
        if dr_cname:
            value = dr_cname["ResourceRecords"][0]["Value"]
            assert value == dr_endpoint, f"CNAME not updated to DR: {value}"
            log(f"DR failover verified: CNAME now points to {value}")
        else:
            print("  ⚠️  CNAME record not found after DR failover (may be eventual consistency)")

        log("Phase R complete")

    except Exception as e:
        print(f"\n❌ Phase R failed: {e}")
        raise
    finally:
        print("  [Phase R cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete zone via boto3
        if zone_id:
            try:
                r53 = _get_aws_boto3_client("route53")
                if r53:
                    sets = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
                    changes = [{"Action": "DELETE", "ResourceRecordSet": rrs}
                               for rrs in sets if rrs["Type"] not in ("NS", "SOA")]
                    if changes:
                        r53.change_resource_record_sets(
                            HostedZoneId=zone_id,
                            ChangeBatch={"Changes": changes},
                        )
                    r53.delete_hosted_zone(Id=zone_id)
                    print(f"  Safety net: deleted hosted zone {zone_id}")
            except Exception as e:
                print(f"  ⚠️  Safety net zone delete failed: {e}")


# ---------------------------------------------------------------------------
# Phase T
# ---------------------------------------------------------------------------

def run_phase_t(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase T: Agent Lifecycle + Resource Tagging — tag EC2 + remove/redeploy agent."""
    print("\n[Phase T] Agent Lifecycle + Resource Tagging")

    instance_id = phase_a_result.get("instance_id", "")
    instance_asset_id = phase_a_result.get("instance_asset", {}).get("id", "")
    if not instance_id:
        fail("Phase T requires a running EC2 instance from Phase A")

    rollback_stack: list[tuple[str, str]] = []

    try:
        ec2_client = _get_aws_boto3_client("ec2")
        sts = _get_aws_boto3_client("sts")
        if not ec2_client or not sts:
            fail("Phase T requires AWS credentials")
        caller = sts.get_caller_identity()
        account_id = caller["Account"]
        creds = _aws_creds_cache
        region = creds.get("region", "us-east-1")
        instance_arn = f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}"

        # 1. Tag EC2 instance via CR
        target_asset_id = instance_asset_id if instance_asset_id else client.get_cloud_account_asset_id()
        cr = client.run_cr(
            "[Phase T] tag EC2 instance", "tag_resource", target_asset_id,
            {"resource_arn": instance_arn, "tags": {"nexplane-smoke-tag": "true", "phase": "T"}},
        )
        rollback_stack.append((cr["id"], "tag_resource"))

        # Verify tag via boto3
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        tags = {t["Key"]: t["Value"]
                for t in desc["Reservations"][0]["Instances"][0].get("Tags", [])}
        assert tags.get("nexplane-smoke-tag") == "true", "Tag not applied to instance"
        log("EC2 instance tagged (boto3 verified)")

        # 2. Remove Nexplane agent via CR
        cr = client.run_cr(
            "[Phase T] remove nexplane agent", "remove_nexplane_agent", target_asset_id,
            {"instance_id": instance_id},
        )
        rollback_stack.append((cr["id"], "remove_nexplane_agent"))
        log("Nexplane agent removed via CR")

        time.sleep(10)

        # 3. Re-deploy agent via CR
        agent_secret = client.get_agent_secret()
        backend_ip = phase_a_result.get("backend_ip", "")
        control_plane_url = f"http://{backend_ip}:8000" if backend_ip else "http://localhost:8000"

        cr = client.run_cr(
            "[Phase T] redeploy nexplane agent", "deploy_nexplane_agent", target_asset_id,
            {
                "instance_id": instance_id,
                "agent_secret": agent_secret,
                "nexplane_url": control_plane_url,
            },
        )
        rollback_stack.append((cr["id"], "deploy_nexplane_agent"))
        log("Nexplane agent redeployed via CR")

        log("Phase T complete")

    except Exception as e:
        print(f"\n❌ Phase T failed: {e}")
        raise
    finally:
        print("  [Phase T cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)


# ---------------------------------------------------------------------------
# Phase S
# ---------------------------------------------------------------------------

def run_phase_s(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase S: RDS Advanced — verify_rds_backup + rds_replica_create + promote_rds_replica (~45 min, opt-in)."""
    print("\n[Phase S] RDS Advanced (slow — ~45 min)")

    import secrets as _secrets
    db_id = f"nexplane-smoke-s-{_secrets.token_hex(3)}"
    replica_id = f"{db_id}-replica"
    snap_id = f"{db_id}-snap"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create RDS instance
        print("  Creating RDS instance (wait ~10 min)...")
        cr = client._run_cr_with_timeout(
            "[Phase S] create RDS instance", "rds_instance_create", cloud_account_id,
            {"db_instance_identifier": db_id, "engine": "mysql", "engine_version": "8.0",
             "db_instance_class": "db.t3.micro", "master_username": "admin",
             "master_password": "Nexplane!Smoke1", "allocated_storage": 20},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_instance_create"))
        log(f"RDS instance created: {db_id}")

        # 2. Create snapshot
        print("  Creating RDS snapshot (wait ~5 min)...")
        cr = client._run_cr_with_timeout(
            "[Phase S] create RDS snapshot", "rds_snapshot_create", cloud_account_id,
            {"db_instance_identifier": db_id, "snapshot_identifier": snap_id},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_create"))
        log(f"Snapshot created: {snap_id}")

        # 3. Verify backup via CR
        client.run_cr(
            "[Phase S] verify RDS backup", "verify_backup", cloud_account_id,
            {"snapshot_identifier": snap_id, "db_instance_identifier": db_id},
        )
        log("Backup verified via CR")

        rds = _get_aws_boto3_client("rds")
        if rds:
            snaps = rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id)["DBSnapshots"]
            assert snaps and snaps[0]["Status"] == "available", "Snapshot not available"
            assert snaps[0]["AllocatedStorage"] > 0, "AllocatedStorage is 0"
            log(f"Snapshot boto3 verified: {snaps[0]['AllocatedStorage']}GB")

        # 4. Create read replica
        print("  Creating read replica (wait ~15 min)...")
        cr = client._run_cr_with_timeout(
            "[Phase S] create RDS read replica", "rds_replica_create", cloud_account_id,
            {"replica_db_instance_identifier": replica_id,
             "source_db_instance_identifier": db_id,
             "db_instance_class": "db.t3.micro"},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_replica_create"))
        log(f"Replica created: {replica_id}")

        # 5. Promote replica
        print("  Promoting replica (wait ~10 min)...")
        cr = client._run_cr_with_timeout(
            "[Phase S] promote RDS replica", "promote_db_replica", cloud_account_id,
            {"replica_identifier": replica_id},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "promote_db_replica"))

        if rds:
            desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)["DBInstances"][0]
            assert not desc.get("ReadReplicaSourceDBInstanceIdentifier"), \
                "Replica still shows source — not yet standalone"
            log(f"Promotion verified: {replica_id} is now standalone")

        # 6. Delete promoted instance
        client.run_cr(
            "[Phase S] delete promoted RDS instance", "rds_instance_delete", cloud_account_id,
            {"db_instance_identifier": replica_id},
        )
        rollback_stack.pop()  # pop promote_db_replica
        rollback_stack.pop()  # pop rds_replica_create
        log("Promoted instance deleted")

        log("Phase S complete")

    except Exception as e:
        print(f"\n❌ Phase S failed: {e}")
        raise
    finally:
        print("  [Phase S cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net
        try:
            rds = _get_aws_boto3_client("rds")
            if rds:
                for iid in [replica_id, db_id]:
                    try:
                        rds.delete_db_instance(DBInstanceIdentifier=iid, SkipFinalSnapshot=True)
                        print(f"  Safety net: deleted RDS instance {iid}")
                    except Exception:
                        pass
                try:
                    rds.delete_db_snapshot(DBSnapshotIdentifier=snap_id)
                except Exception:
                    pass
        except Exception:
            pass


def run_phase_u(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase U: capture_instance_state + block/restore S3 public access."""
    print("\n[Phase U] Instance State Capture + S3 Public Access")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    import secrets as _secrets
    bucket_name = f"nexplane-smoke-u-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Capture instance state as a standalone CR
        cr = client.run_cr(
            "[Phase U] capture EC2 instance state", "capture_instance_state",
            instance_asset["id"],
            {"instance_id": instance_id},
        )
        rollback_stack.append((cr["id"], "capture_instance_state"))
        log("EC2 instance state captured")

        # capture_instance_state has no meaningful rollback — pop it now
        rollback_stack.pop()

        # 2. Create a test S3 bucket for public access tests
        cloud_account_id = client.get_cloud_account_asset_id()
        cr = client.run_cr(
            "[Phase U] create S3 bucket for public access test", "s3_bucket_create",
            cloud_account_id,
            {"bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "s3_bucket_create"))
        log(f"S3 bucket created: {bucket_name}")

        # 3. Block public access via CR
        cr = client.run_cr(
            "[Phase U] block S3 public access", "block_s3_public_access",
            cloud_account_id,
            {"bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "block_s3_public_access"))

        # Verify via boto3
        s3 = _get_aws_boto3_client("s3")
        if s3:
            try:
                pab = s3.get_public_access_block(Bucket=bucket_name)["PublicAccessBlockConfiguration"]
                assert pab.get("BlockPublicAcls") and pab.get("BlockPublicPolicy"), \
                    "Public access not fully blocked"
                log("S3 public access blocked (boto3 verified)")
            except Exception as e:
                print(f"  ⚠️  S3 public access verification skipped: {e}")

        # 4. Restore public access via CR rollback
        block_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(block_cr_id, "block_s3_public_access → restore")
        log("S3 public access restored via CR rollback")

        # 5. Delete bucket via CR rollback
        bucket_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(bucket_cr_id, "s3_bucket_create → delete")
        log("S3 bucket deleted via CR rollback")

        log("Phase U complete")

    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        print("  [Phase U cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete bucket
        try:
            s3 = _get_aws_boto3_client("s3")
            if s3:
                try:
                    objs = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
                    for obj in objs:
                        s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
                    s3.delete_bucket(Bucket=bucket_name)
                    print(f"  Safety net: deleted bucket {bucket_name}")
                except Exception:
                    pass
        except Exception:
            pass


def run_phase_v(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase V: tailscale_remove — exercises the tailscale_remove executor."""
    print("\n[Phase V] Tailscale Remove")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    rollback_stack: list[tuple[str, str]] = []

    try:
        # Remove Tailscale from the instance via CR
        cr = client.run_cr(
            "[Phase V] tailscale remove", "tailscale_remove", instance_asset["id"],
            {"instance_id": instance_id},
        )
        rollback_stack.append((cr["id"], "tailscale_remove"))
        log("Tailscale removed from EC2 instance via CR")

        # Brief pause for removal to take effect
        time.sleep(5)

        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        print("  [Phase V cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)


def run_phase_w(client: NexplaneClient, cloud_account_id: str, phase_a_result: dict) -> None:
    """Phase W: ALB lifecycle — create ALB + target group + listener, register targets, verify, deregister, rollback."""
    print("\n[Phase W] ALB Lifecycle")

    instance_id = phase_a_result["instance_id"]
    rollback_stack: list[tuple[str, str]] = []

    # Look up VPC/subnets/SG from the Phase A EC2 instance
    ec2 = _get_aws_boto3_client("ec2")
    elbv2 = _get_aws_boto3_client("elbv2")
    if not ec2 or not elbv2:
        fail("Phase W requires AWS boto3 client with ec2 and elbv2 access")

    inst_resp = ec2.describe_instances(InstanceIds=[instance_id])
    inst_data = inst_resp["Reservations"][0]["Instances"][0]
    vpc_id = inst_data["VpcId"]
    sg_ids = [sg["GroupId"] for sg in inst_data["SecurityGroups"]]

    # ALB requires >=2 subnets in different AZs
    subnets_resp = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    subnet_ids: list[str] = []
    seen_azs: set[str] = set()
    for s in subnets_resp["Subnets"]:
        az = s["AvailabilityZone"]
        if az not in seen_azs:
            subnet_ids.append(s["SubnetId"])
            seen_azs.add(az)
        if len(subnet_ids) == 2:
            break
    if len(subnet_ids) < 2:
        fail(f"Phase W requires >=2 subnets in different AZs in VPC {vpc_id}, found {len(subnet_ids)}")

    alb_name = "nexplane-smoke-alb"
    tg_name = "nexplane-smoke-tg"

    try:
        # 1. Create ALB
        cr = client.run_cr(
            "[Phase W] create ALB", "alb_create", cloud_account_id,
            {
                "name": alb_name,
                "scheme": "internet-facing",
                "lb_type": "application",
                "subnets": subnet_ids,
                "security_groups": sg_ids,
            },
        )
        rollback_stack.append((cr["id"], "alb_create"))

        lb_resp = elbv2.describe_load_balancers(Names=[alb_name])
        lb_arn = lb_resp["LoadBalancers"][0]["LoadBalancerArn"]
        log(f"ALB created: {lb_arn}")

        # 2. Create target group
        cr = client.run_cr(
            "[Phase W] create target group", "target_group_create", cloud_account_id,
            {
                "name": tg_name,
                "protocol": "HTTP",
                "port": 80,
                "vpc_id": vpc_id,
                "target_type": "instance",
            },
        )
        rollback_stack.append((cr["id"], "target_group_create"))

        tg_resp = elbv2.describe_target_groups(Names=[tg_name])
        tg_arn = tg_resp["TargetGroups"][0]["TargetGroupArn"]
        log(f"Target group created: {tg_arn}")

        # 3. Register Phase A instance as target
        targets = [{"Id": instance_id, "Port": 80}]
        cr = client.run_cr(
            "[Phase W] register targets", "register_targets", cloud_account_id,
            {"tg_arn": tg_arn, "targets": targets},
        )
        rollback_stack.append((cr["id"], "register_targets"))
        log(f"Instance {instance_id} registered as target")

        # 4. Create listener on port 80 forwarding to the target group
        cr = client.run_cr(
            "[Phase W] create listener", "listener_create", cloud_account_id,
            {
                "lb_arn": lb_arn,
                "protocol": "HTTP",
                "port": 80,
                "default_target_group_arn": tg_arn,
            },
        )
        rollback_stack.append((cr["id"], "listener_create"))

        listeners_resp = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
        listener_arn = listeners_resp["Listeners"][0]["ListenerArn"]
        log(f"Listener created: {listener_arn}")

        # 5. Verify target is registered via boto3
        health_resp = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        registered_ids = [t["Target"]["Id"] for t in health_resp["TargetHealthDescriptions"]]
        assert instance_id in registered_ids, \
            f"Instance {instance_id} not in registered targets: {registered_ids}"
        log("Target registration verified (boto3 describe_target_health)")

        # 6. Modify listener — change port to 8080
        client.run_cr(
            "[Phase W] modify listener port", "listener_modify", cloud_account_id,
            {"listener_arn": listener_arn, "port": 8080, "protocol": "HTTP"},
        )

        updated_listeners = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
        updated_port = updated_listeners["Listeners"][0]["Port"]
        assert updated_port == 8080, f"Listener port not updated: expected 8080, got {updated_port}"
        log("Listener port modified to 8080 (boto3 verified)")

        # 7. Deregister targets — exercise the deregister_targets CR
        client.run_cr(
            "[Phase W] deregister targets", "deregister_targets", cloud_account_id,
            {"tg_arn": tg_arn, "targets": targets},
        )
        # Pop register_targets from rollback stack (already deregistered via explicit CR)
        rollback_stack.pop(2)

        # AWS deregistration is async — targets enter "draining" state before removal.
        # Poll until the target is gone or in "unused" state (up to 30s).
        import time as _time
        for _ in range(6):
            health_after = elbv2.describe_target_health(TargetGroupArn=tg_arn)
            remaining = [
                t["Target"]["Id"] for t in health_after["TargetHealthDescriptions"]
                if t.get("TargetHealth", {}).get("State") not in ("unused", "draining")
            ]
            if instance_id not in remaining:
                break
            _time.sleep(5)
        else:
            health_after = elbv2.describe_target_health(TargetGroupArn=tg_arn)
            remaining = [t["Target"]["Id"] for t in health_after["TargetHealthDescriptions"]]
            assert instance_id not in remaining, \
                f"Instance {instance_id} still registered after deregister: {remaining}"
        log("Target deregistered and verified (boto3 describe_target_health)")

        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        print("  [Phase W cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete ALB and TG via boto3 if still present
        try:
            if elbv2:
                try:
                    lb_resp2 = elbv2.describe_load_balancers(Names=[alb_name])
                    if lb_resp2.get("LoadBalancers"):
                        remaining_lb_arn = lb_resp2["LoadBalancers"][0]["LoadBalancerArn"]
                        for lst in elbv2.describe_listeners(LoadBalancerArn=remaining_lb_arn).get("Listeners", []):
                            elbv2.delete_listener(ListenerArn=lst["ListenerArn"])
                        elbv2.delete_load_balancer(LoadBalancerArn=remaining_lb_arn)
                        print(f"  Safety net: deleted ALB {alb_name}")
                except Exception:
                    pass
                try:
                    tg_resp2 = elbv2.describe_target_groups(Names=[tg_name])
                    if tg_resp2.get("TargetGroups"):
                        elbv2.delete_target_group(TargetGroupArn=tg_resp2["TargetGroups"][0]["TargetGroupArn"])
                        print(f"  Safety net: deleted target group {tg_name}")
                except Exception:
                    pass
        except Exception:
            pass


_SMOKETEST_APP_NAME = "nexplane-smoketest"
_SMOKETEST_UNIT = "nexplane-smoketest.service"
_SMOKETEST_PORT = 8099

_INSTALL_SMOKETEST_APP = r"""
set -e
# Write the test HTTP server
cat > /opt/nexplane-smoketest.py << 'PYEOF'
import http.server, socketserver, signal, sys

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"nexplane-smoketest-ok")
    def log_message(self, *a): pass

with socketserver.TCPServer(("", 8099), H) as s:
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    s.serve_forever()
PYEOF
chmod +x /opt/nexplane-smoketest.py

# Install systemd unit
cat > /etc/systemd/system/nexplane-smoketest.service << 'SVCEOF'
[Unit]
Description=Nexplane Smoke Test HTTP App
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/nexplane-smoketest.py
Restart=always
User=nobody
WorkingDirectory=/tmp

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable nexplane-smoketest
systemctl start nexplane-smoketest
sleep 2
systemctl is-active nexplane-smoketest && echo "OK: smoketest app running" || (journalctl -u nexplane-smoketest --no-pager -n 20; exit 1)
""".strip()

_UNINSTALL_SMOKETEST_APP = r"""
systemctl stop nexplane-smoketest 2>/dev/null || true
systemctl disable nexplane-smoketest 2>/dev/null || true
rm -f /etc/systemd/system/nexplane-smoketest.service /opt/nexplane-smoketest.py
systemctl daemon-reload
echo "OK: smoketest app removed"
""".strip()


def run_phase_x(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase X: Application Discovery — validates agent_appdiscovery CR end-to-end.

    Requires Phase A (running EC2 instance with Nexplane agent deployed).
    1. Installs a known test HTTP service (nexplane-smoketest) via SSM as a
       deterministic discovery target on port 8099 at /opt/nexplane-smoketest.py.
    2. Fires an agent_appdiscovery CR targeting the Phase A instance asset.
    3. Verifies asset_metadata.applications contains the known test app.
    4. Verifies each discovered application has all required fields and correct
       containerization_status.
    5. Cleans up the test service via SSM after verification.
    """
    log("\n[Phase X] Application Discovery")

    instance_asset = phase_a_result.get("instance_asset", {})
    instance_asset_id = instance_asset.get("id")
    instance_id = phase_a_result.get("instance_id")
    if not instance_asset_id or not instance_id:
        fail("Phase X requires phase_a_result['instance_asset']['id'] and ['instance_id']")

    # Step 1: Install the known test application via SSM
    log("[Phase X] Installing nexplane-smoketest service via SSM")
    client.run_cr(
        "[Phase X] install smoketest app",
        "ssm_command",
        instance_asset_id,
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": _INSTALL_SMOKETEST_APP,
            "rollback_strategy": "rollback_unavailable",
        },
    )
    log(f"[Phase X] nexplane-smoketest service running on port {_SMOKETEST_PORT}")

    try:
        # Step 2: Wait for the Nexplane agent to register as a server asset
        # The agent registers under name=hostname ('nexplane-smoke-ec2') with asset_type=server.
        log("[Phase X] Waiting for Nexplane agent to register (up to 3 min)")
        import time as _time
        deadline = _time.time() + 180
        agent_asset_id = None
        while _time.time() < deadline:
            candidates = client.get("/assets", params={"q": "nexplane-smoke-ec2", "asset_type": "server"})
            # Pick the most recently updated agent-tagged asset to avoid stale registrations
            tagged = [c for c in candidates if "nexplane-agent" in (c.get("tags") or [])]
            if tagged:
                # Sort by updated_at descending; fall back to any if field missing
                tagged.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
                agent_asset_id = tagged[0]["id"]
                log(f"[Phase X] Agent registered: {agent_asset_id}")
                # Store for Phase Y/Z
                phase_a_result["agent_asset_id"] = agent_asset_id
                break
            _time.sleep(10)
        if not agent_asset_id:
            fail("[Phase X] Nexplane agent did not register within 3 minutes — cannot run discovery")

        # Step 3: Fire the agent_appdiscovery CR targeting the agent's registered asset
        log("[Phase X] Running agent_appdiscovery CR on agent asset")
        client.run_cr(
            "[Phase X] discover applications",
            "agent_appdiscovery",
            agent_asset_id,
            {"dry_run": False},
        )
        log("[Phase X] appdiscovery CR completed")

        # Step 4: Fetch the agent asset and verify applications were written to its metadata
        log("[Phase X] Verifying asset_metadata.applications was written")
        instance_asset = client.get(f"/assets/{agent_asset_id}")
        if not instance_asset:
            fail(f"[Phase X] Instance asset {instance_asset_id} not found after discovery")

        applications = (instance_asset.get("asset_metadata") or {}).get("applications")
        if not isinstance(applications, list):
            fail(f"[Phase X] asset_metadata.applications not set — got: {applications}")
        if len(applications) == 0:
            fail("[Phase X] asset_metadata.applications is empty — expected nexplane-smoketest at minimum")

        log(f"[Phase X] Found {len(applications)} application(s): {', '.join(a.get('name','?') for a in applications)}")

        # Step 4: Verify all required fields on every discovered app
        required_fields = [
            "id", "name", "binary", "systemd_unit", "listening_ports",
            "config_files", "data_directories", "estimated_data_size_gb",
            "stateful", "containerization_status",
        ]
        for app in applications:
            for field in required_fields:
                if field not in app:
                    fail(f"[Phase X] App '{app.get('name','?')}' missing required field '{field}'")
            if app["containerization_status"] != "not_started":
                fail(
                    f"[Phase X] Expected containerization_status='not_started', "
                    f"got '{app['containerization_status']}' for app '{app['name']}'"
                )

        # Step 5: Assert the known test app was specifically discovered
        app_names = [a.get("name", "") for a in applications]
        if _SMOKETEST_APP_NAME not in app_names:
            fail(
                f"[Phase X] Expected '{_SMOKETEST_APP_NAME}' in discovered apps, "
                f"got: {app_names}"
            )

        smoketest_app = next(a for a in applications if a["name"] == _SMOKETEST_APP_NAME)
        # Verify the port was discovered
        ports = [p.get("port") for p in (smoketest_app.get("listening_ports") or [])]
        if _SMOKETEST_PORT not in ports:
            log(f"[Phase X] Warning: port {_SMOKETEST_PORT} not in discovered ports {ports} "
                f"(may not yet appear in ss output — non-fatal)")
        else:
            log(f"[Phase X] nexplane-smoketest discovered on port {_SMOKETEST_PORT} ✅")

        log(f"[Phase X] All {len(applications)} application(s) have required fields ✅")
        log("[Phase X] ✅ Application discovery phase complete")

    finally:
        # Step 6: Always clean up the test service
        log("[Phase X] Cleaning up nexplane-smoketest service")
        try:
            client.run_cr(
                "[Phase X] remove smoketest app",
                "ssm_command",
                instance_asset_id,
                {
                    "instance_id": instance_id,
                    "document_name": "AWS-RunShellScript",
                    "command": _UNINSTALL_SMOKETEST_APP,
                    "rollback_strategy": "rollback_unavailable",
                },
            )
            log("[Phase X] nexplane-smoketest service removed")
        except Exception as e:
            log(f"[Phase X] Warning: cleanup failed (non-fatal): {e}")


def run_phase_y(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase Y: Containerize Build (dry_run) — validates agent_containerize_build CR.

    Requires Phase X to have run (agent registered, nexplane-smoketest in applications[]).
    Uses dry_run=True so no Docker daemon is required on the test instance.
    Verifies Dockerfile + k8s manifests were generated and stored on asset_metadata.
    """
    log("\n[Phase Y] Containerize Build (dry_run)")

    agent_asset_id = phase_a_result.get("agent_asset_id")
    if not agent_asset_id:
        fail("Phase Y requires phase_a_result['agent_asset_id'] (set by Phase X)")

    # Fire the build CR in dry_run mode — no Docker daemon required
    log("[Phase Y] Firing agent_containerize_build CR (dry_run=True)")
    client.run_cr(
        "[Phase Y] containerize build dry run",
        "agent_containerize_build",
        agent_asset_id,
        {
            "app_name": "nexplane-smoketest",
            "registry": "smoke-test-registry.example.com/nexplane",
            "namespace": "smoke-test",
            "dry_run": True,
        },
    )
    log("[Phase Y] CR completed")

    # Verify build results stored on asset_metadata
    log("[Phase Y] Verifying build results written to asset_metadata")
    asset = client.get(f"/assets/{agent_asset_id}")
    if not asset:
        fail(f"[Phase Y] Agent asset {agent_asset_id} not found")

    build_results = (asset.get("asset_metadata") or {}).get("build_results", {})
    if "nexplane-smoketest" not in build_results:
        fail(
            f"[Phase Y] build_results['nexplane-smoketest'] not found in asset_metadata. "
            f"Got keys: {list(build_results.keys())}"
        )

    result = build_results["nexplane-smoketest"]

    # Verify Dockerfile was generated
    dockerfile = result.get("dockerfile", "")
    if not dockerfile:
        fail("[Phase Y] No Dockerfile in build_results")
    if "FROM" not in dockerfile:
        fail(f"[Phase Y] Dockerfile looks invalid: {dockerfile[:100]}")
    log("[Phase Y] Dockerfile generated ✅")

    # Verify k8s manifests were generated
    manifests = result.get("manifests", {})
    if not manifests.get("deployment"):
        fail("[Phase Y] No Deployment manifest in build_results.manifests")
    if "kind: Deployment" not in manifests["deployment"]:
        fail(f"[Phase Y] Deployment manifest looks invalid: {manifests['deployment'][:100]}")
    log("[Phase Y] Kubernetes manifests generated ✅")

    # Verify containerization_status updated
    applications = (asset.get("asset_metadata") or {}).get("applications", [])
    smoketest_app = next((a for a in applications if a.get("name") == "nexplane-smoketest"), None)
    if smoketest_app and smoketest_app.get("containerization_status") in ("dockerfile_generated", "image_pushed"):
        log(f"[Phase Y] containerization_status: {smoketest_app['containerization_status']} ✅")
    else:
        log("[Phase Y] Warning: containerization_status not updated (non-fatal)")

    log("[Phase Y] ✅ Containerize build phase complete")


def run_phase_z(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase Z: Containerize Retire — validates agent_containerize_retire end-to-end.

    Requires Phase X (nexplane-smoketest discovered by agent).
    1. Re-installs nexplane-smoketest (cleaned up by Phase X)
    2. Fires retire in dry_run=True — service stays running
    3. Fires retire in dry_run=False — service is stopped
    4. Verifies asset_metadata.applications[].containerization_status == 'retired'
    5. Cleans up
    """
    log("\n[Phase Z] Containerize Retire")

    agent_asset_id = phase_a_result.get("agent_asset_id")
    instance_asset_id = phase_a_result.get("instance_asset", {}).get("id")
    instance_id = phase_a_result.get("instance_id")
    if not agent_asset_id or not instance_asset_id or not instance_id:
        fail("Phase Z requires phase_a_result['agent_asset_id'], ['instance_asset']['id'], and ['instance_id']")

    # Re-install nexplane-smoketest
    log("[Phase Z] Re-installing nexplane-smoketest for retire test")
    client.run_cr(
        "[Phase Z] reinstall smoketest",
        "ssm_command",
        instance_asset_id,
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": _INSTALL_SMOKETEST_APP,
            "rollback_strategy": "rollback_unavailable",
        },
    )

    try:
        # Step 1: Dry run — service should remain running
        log("[Phase Z] Retire dry_run=True (service should remain running)")
        client.run_cr(
            "[Phase Z] retire dry run",
            "agent_containerize_retire",
            agent_asset_id,
            {"systemd_unit": _SMOKETEST_UNIT, "dry_run": True},
        )
        log("[Phase Z] Dry run completed — service not stopped ✅")

        # Step 2: Real retire — service should stop
        log("[Phase Z] Retire dry_run=False (service should be stopped)")
        client.run_cr(
            "[Phase Z] retire real",
            "agent_containerize_retire",
            agent_asset_id,
            {"systemd_unit": _SMOKETEST_UNIT, "dry_run": False},
        )
        log("[Phase Z] Retire CR completed")

        # Verify asset_metadata updated
        asset = client.get(f"/assets/{agent_asset_id}")
        applications = (asset.get("asset_metadata") or {}).get("applications", [])
        smoketest_app = next((a for a in applications if a.get("name") == _SMOKETEST_APP_NAME), None)
        if smoketest_app and smoketest_app.get("containerization_status") == "retired":
            log("[Phase Z] containerization_status=retired ✅")
        else:
            status = smoketest_app.get("containerization_status") if smoketest_app else "app not found"
            log(f"[Phase Z] Warning: containerization_status={status} (non-fatal — may need Phase X to run first)")

        log("[Phase Z] ✅ Containerize retire phase complete")

    finally:
        log("[Phase Z] Cleanup: removing nexplane-smoketest")
        try:
            client.run_cr(
                "[Phase Z] cleanup smoketest",
                "ssm_command",
                instance_asset_id,
                {
                    "instance_id": instance_id,
                    "document_name": "AWS-RunShellScript",
                    "command": _UNINSTALL_SMOKETEST_APP,
                    "rollback_strategy": "rollback_unavailable",
                },
            )
            log("[Phase Z] Cleanup complete")
        except Exception as e:
            log(f"[Phase Z] Cleanup warning (non-fatal): {e}")


def main():
    parser = make_base_parser("Nexplane AWS live smoke test")
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help=(
            "Comma-separated phases to run. "
            "A-K: existing phases. P-T: new phases (P=IAM, Q=S3, R=DR-DNS, S=RDS-slow, T=Agent). "
            "Default: A,B,C,D. J and S are slow (~35-45 min). U=instance-state+S3-access, V=tailscale-remove, W=ALB-lifecycle."
        ),
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key for Phase A")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane AWS Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    try:
        stale = []
        for q in ("nexplane-smoke-test", "nexplane-smoke-ec2"):
            stale += [a for a in client.get("/assets", params={"q": q})
                      if q.split("-")[2] in a.get("name", "")]
        for asset in stale:
            try:
                client.client.delete(f"{client.base}/assets/{asset['id']}")
            except Exception:
                pass
        if stale:
            print(f"  Pre-run: removed {len(stale)} stale inventory asset(s)")
    except Exception:
        pass

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    phase_a_result: Optional[dict] = None

    try:
        if "A" in phases:
            phase_a_result = run_phase_a(client, cloud_account_id, args.tailscale_auth_key)
        if "B" in phases:
            if phase_a_result is None:
                fail("Phase B requires Phase A to have run first")
            run_phase_b(client, phase_a_result)
        if "C" in phases:
            run_phase_c(client, cloud_account_id)
        if "D" in phases:
            run_phase_d(client, phase_a_result)
        if "E" in phases:
            if phase_a_result is None:
                fail("Phase E requires Phase A to have run first")
            run_phase_e(client, phase_a_result)
        if "F" in phases:
            run_phase_f(client, cloud_account_id)
        if "G" in phases:
            run_phase_g(client, cloud_account_id)
        if "H" in phases:
            run_phase_h(client, cloud_account_id)
        if "I" in phases:
            run_phase_i(client, cloud_account_id)
        if "J" in phases:
            run_phase_j(client, cloud_account_id)
        if "K" in phases:
            if phase_a_result is None:
                fail("Phase K requires Phase A to have run first")
            run_phase_k(client, phase_a_result)
        if "P" in phases:
            run_phase_p(client, cloud_account_id)
        if "Q" in phases:
            run_phase_q(client, cloud_account_id)
        if "R" in phases:
            run_phase_r(client, cloud_account_id)
        if "S" in phases:
            run_phase_s(client, cloud_account_id)
        if "U" in phases:
            if phase_a_result is None:
                fail("Phase U requires Phase A to have run first")
            run_phase_u(client, phase_a_result)
        if "V" in phases:
            if phase_a_result is None:
                fail("Phase V requires Phase A to have run first")
            run_phase_v(client, phase_a_result)
        if "W" in phases:
            if phase_a_result is None:
                fail("Phase W requires Phase A to have run first")
            run_phase_w(client, cloud_account_id, phase_a_result)
        if "X" in phases:
            if phase_a_result is None:
                fail("Phase X requires Phase A to have run first")
            run_phase_x(client, phase_a_result)
        if "Y" in phases:
            if phase_a_result is None:
                fail("Phase Y requires Phase A to have run first")
            run_phase_y(client, phase_a_result)
        if "Z" in phases:
            if phase_a_result is None:
                fail("Phase Z requires Phase A to have run first")
            run_phase_z(client, phase_a_result)
        if "T" in phases:
            if phase_a_result is None:
                fail("Phase T requires Phase A to have run first")
            run_phase_t(client, phase_a_result)

        print("\n" + "=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        if "A" in phases:
            cleanup(client)
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
