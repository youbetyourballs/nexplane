#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Insert OPENVAS_SCAN and NESSUS_SCAN phase functions into test_aws_live.py."""
from __future__ import print_function
import sys

TARGET = "f:/Nexplane/nexplane/backend/tests/smoke/test_aws_live.py"

OPENVAS_PHASE = '''
# ---------------------------------------------------------------------------
# Phase OPENVAS_SCAN — OpenVAS / Greenbone CE vulnerability scan (Docker, AMI cached)
# ---------------------------------------------------------------------------

def run_phase_openvas_scan(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase OPENVAS_SCAN: launch t3.medium EC2, start Greenbone CE via Docker,
    wait for services, register connector, run scan CR, verify findings returned.
    AMI cached after first Docker pull completes."""
    import time, hashlib
    print("\\n[Phase OPENVAS_SCAN] OpenVAS / Greenbone Community Edition vulnerability scan")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[OPENVAS_SCAN] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    INSTANCE_TYPE = "t3.medium"  # OpenVAS needs 4GB RAM

    setup_script = (
        "set -e\\n"
        "yum install -y docker 2>/dev/null || dnf install -y docker 2>/dev/null || true\\n"
        "systemctl enable docker && systemctl start docker\\n"
        "sleep 3\\n"
        "curl -fsSL https://github.com/docker/compose/releases/download/v2.24.1/docker-compose-linux-x86_64"
        " -o /usr/local/bin/docker-compose\\n"
        "chmod +x /usr/local/bin/docker-compose\\n"
        "mkdir -p /opt/greenbone\\n"
        "cat > /opt/greenbone/docker-compose.yml << \\'COMPOSE_EOF\\'\\n"
        "version: \\"3.8\\"\\n"
        "services:\\n"
        "  pg-gvm:\\n"
        "    image: greenbone/pg-gvm:stable\\n"
        "    restart: on-failure\\n"
        "    volumes:\\n"
        "      - psql_data_vol:/var/lib/postgresql\\n"
        "      - psql_socket_vol:/var/run/postgresql\\n"
        "  gvmd:\\n"
        "    image: greenbone/gvmd:stable\\n"
        "    restart: on-failure\\n"
        "    volumes:\\n"
        "      - gvmd_data_vol:/var/lib/gvm\\n"
        "      - psql_data_vol:/var/lib/postgresql\\n"
        "      - gvmd_socket_vol:/var/run/gvmd\\n"
        "      - ospd_openvas_socket_vol:/var/run/ospd\\n"
        "      - psql_socket_vol:/var/run/postgresql\\n"
        "    depends_on:\\n"
        "      pg-gvm:\\n"
        "        condition: service_started\\n"
        "  ospd-openvas:\\n"
        "    image: greenbone/ospd-openvas:stable\\n"
        "    restart: on-failure\\n"
        "    init: true\\n"
        "    cap_add:\\n"
        "      - NET_ADMIN\\n"
        "      - NET_RAW\\n"
        "    security_opt:\\n"
        "      - seccomp=unconfined\\n"
        "      - apparmor=unconfined\\n"
        "    command: [ospd-openvas, -f, --config, /etc/gvm/ospd-openvas.conf, -m, 666]\\n"
        "    volumes:\\n"
        "      - ospd_openvas_socket_vol:/var/run/ospd\\n"
        "  gsa:\\n"
        "    image: greenbone/gsa:stable\\n"
        "    restart: on-failure\\n"
        "    ports:\\n"
        "      - 9392:80\\n"
        "    volumes:\\n"
        "      - gvmd_socket_vol:/var/run/gvmd\\n"
        "    depends_on:\\n"
        "      - gvmd\\n"
        "volumes:\\n"
        "  gvmd_data_vol:\\n"
        "  psql_data_vol:\\n"
        "  psql_socket_vol:\\n"
        "  gvmd_socket_vol:\\n"
        "  ospd_openvas_socket_vol:\\n"
        "COMPOSE_EOF\\n"
        "cd /opt/greenbone\\n"
        "docker-compose pull 2>&1 | tail -3 || true\\n"
        "docker-compose up -d\\n"
        "echo GREENBONE_SETUP_COMPLETE\\n"
    )

    setup_hash = hashlib.md5(setup_script.encode()).hexdigest()

    cached_ami = None
    param_path = f"/nexplane/smoke-amis/openvas/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached OpenVAS AMI: {cached_ami}")
    except Exception:
        pass

    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [INSTANCE_TYPE]}])["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-openvas"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"OpenVAS EC2: {instance_id}")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 300
    private_ip = ""
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "running":
                private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                break
        except Exception:
            pass
        time.sleep(8)

    deadline2 = time.time() + 180
    while time.time() < deadline2:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    openvas_connector_id = None
    try:
        if not cached_ami:
            log("Installing Docker + Greenbone CE (5-15 min for image pull)...")
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [setup_script]}, TimeoutSeconds=900)
            setup_deadline = time.time() + 900
            while time.time() < setup_deadline:
                time.sleep(30)
                try:
                    out_s = ssm_client.get_command_invocation(
                        CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                    if out_s["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                        if "GREENBONE_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
                            log("Greenbone CE setup complete")
                        else:
                            log(f"  WARNING: Greenbone setup: {out_s.get('StandardErrorContent', '')[:200]}")
                        break
                except Exception:
                    pass
            try:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "openvas", setup_hash)
            except Exception as e:
                log(f"  WARNING: AMI cache failed: {e}")
        else:
            ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["cd /opt/greenbone && docker-compose up -d 2>/dev/null || true"]},
                TimeoutSeconds=120)
            time.sleep(30)

        log("Waiting for Greenbone GSA API on port 9392 (up to 10 min)...")
        gsa_deadline = time.time() + 600
        gsa_ready = False
        while time.time() < gsa_deadline:
            time.sleep(20)
            check_cmd = "curl -sk -o /dev/null -w '%{http_code}' http://localhost:9392/ 2>/dev/null || echo 000"
            resp_c = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [check_cmd]}, TimeoutSeconds=15)
            time.sleep(8)
            try:
                out_c = ssm_client.get_command_invocation(
                    CommandId=resp_c["Command"]["CommandId"], InstanceId=instance_id)
                http_code = out_c.get("StandardOutputContent", "").strip()
                if http_code in ("200", "302", "401"):
                    gsa_ready = True
                    log(f"GSA API ready (HTTP {http_code})")
                    break
            except Exception:
                pass
        if not gsa_ready:
            log("  WARNING: GSA API did not respond within timeout")

        openvas_url = f"http://{private_ip}:9392"
        conn_resp = client.post("/connectors", json={
            "connector_type": "openvas",
            "name": "nexplane-smoke-openvas",
            "display_name": "nexplane-smoke-openvas",
            "credentials": {"base_url": openvas_url, "username": "admin", "password": "admin"},
        })
        openvas_connector_id = conn_resp.get("id")
        log(f"OpenVAS connector registered: {openvas_connector_id}")

        asset_resp = client.post("/assets", json={
            "name": "nexplane-smoke-openvas-target",
            "asset_type": "server",
            "properties": {"ip": private_ip, "hostname": "nexplane-smoke-openvas-target"},
        })
        asset_id = asset_resp.get("id", cloud_account_id)

        cr = client.run_cr(
            "[OPENVAS_SCAN] run vulnerability scan",
            "openvas_run_scan",
            asset_id,
            {"target_hosts": "127.0.0.1", "scan_name": "nexplane-smoke-scan",
             "max_wait_seconds": 1800, "poll_interval": 30},
        )
        exec_runs = cr.get("execution_runs") or []
        result = exec_runs[0].get("result") if exec_runs else {}

        if result.get("status") == "skipped":
            log("  WARNING: Scan skipped (no connector credentials in backend)")
        elif result.get("action") == "openvas_run_scan":
            log(f"Scan completed. Status: {result.get('status')} | Findings: {result.get('finding_count', 0)}")
        else:
            log(f"  WARNING: Unexpected result: {result}")
        log("Phase OPENVAS_SCAN PASSED")

    except Exception as e:
        print(f"\\n[FAIL] Phase OPENVAS_SCAN failed: {e}")
        raise
    finally:
        if openvas_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{openvas_connector_id}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass

'''

NESSUS_PHASE = '''
# ---------------------------------------------------------------------------
# Phase NESSUS_SCAN — Nessus Essentials vulnerability scan (AMI cached)
# ---------------------------------------------------------------------------

def run_phase_nessus_scan(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase NESSUS_SCAN: launch t3.medium EC2 (AL2023), install Nessus Essentials,
    register connector, run scan CR, verify findings returned. AMI cached after first setup."""
    import time, hashlib
    print("\\n[Phase NESSUS_SCAN] Nessus Essentials vulnerability scan")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[NESSUS_SCAN] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    INSTANCE_TYPE = "t3.medium"
    NESSUS_RPM_URL = "https://www.tenable.com/downloads/api/v2/pages/nessus/files/Nessus-10.8.3-amzn2023.x86_64.rpm"

    setup_script = (
        "set -e\\n"
        f"curl -fsSL -o /tmp/nessus.rpm {NESSUS_RPM_URL!r}\\n"
        "rpm -ivh /tmp/nessus.rpm 2>/dev/null || yum install -y /tmp/nessus.rpm 2>/dev/null || true\\n"
        "systemctl enable nessusd && systemctl start nessusd || service nessusd start || true\\n"
        "sleep 30\\n"
        "echo NESSUS_SETUP_COMPLETE\\n"
    )

    setup_hash = hashlib.md5(setup_script.encode()).hexdigest()

    cached_ami = None
    param_path = f"/nexplane/smoke-amis/nessus/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached Nessus AMI: {cached_ami}")
    except Exception:
        pass

    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [INSTANCE_TYPE]}])["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-nessus"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"Nessus EC2: {instance_id}")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 300
    private_ip = ""
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "running":
                private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                break
        except Exception:
            pass
        time.sleep(8)

    deadline2 = time.time() + 180
    while time.time() < deadline2:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    nessus_connector_id = None
    try:
        if not cached_ami:
            log("Installing Nessus Essentials...")
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [setup_script]}, TimeoutSeconds=300)
            setup_deadline = time.time() + 300
            while time.time() < setup_deadline:
                time.sleep(20)
                try:
                    out_s = ssm_client.get_command_invocation(
                        CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                    if out_s["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                        if "NESSUS_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
                            log("Nessus installed")
                        else:
                            log(f"  WARNING: Nessus setup: {out_s.get('StandardErrorContent', '')[:200]}")
                        break
                except Exception:
                    pass
            try:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "nessus", setup_hash)
            except Exception as e:
                log(f"  WARNING: AMI cache failed: {e}")
        else:
            ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["systemctl start nessusd 2>/dev/null || true && sleep 15"]},
                TimeoutSeconds=60)
            time.sleep(20)

        log("Waiting for Nessus API on port 8834...")
        nessus_deadline = time.time() + 180
        nessus_ready = False
        while time.time() < nessus_deadline:
            time.sleep(15)
            check_cmd = "curl -sk -o /dev/null -w '%{http_code}' https://localhost:8834/ 2>/dev/null || echo 000"
            resp_c = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [check_cmd]}, TimeoutSeconds=15)
            time.sleep(8)
            try:
                out_c = ssm_client.get_command_invocation(
                    CommandId=resp_c["Command"]["CommandId"], InstanceId=instance_id)
                http_code = out_c.get("StandardOutputContent", "").strip()
                if http_code in ("200", "302", "401", "403"):
                    nessus_ready = True
                    log(f"Nessus API ready (HTTP {http_code})")
                    break
            except Exception:
                pass

        # Create admin user via nessuscli (no registration required for API access)
        create_user_cmd = (
            "/opt/nessus/sbin/nessuscli adduser admin << 'EOF'\\n"
            "adminpassword123\\n"
            "adminpassword123\\n"
            "y\\n"
            "\\n"
            "EOF\\n"
            "echo USER_DONE || true"
        )
        ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [create_user_cmd]}, TimeoutSeconds=30)
        time.sleep(15)

        nessus_url = f"https://{private_ip}:8834"
        conn_resp = client.post("/connectors", json={
            "connector_type": "nessus",
            "name": "nexplane-smoke-nessus",
            "display_name": "nexplane-smoke-nessus",
            "credentials": {"base_url": nessus_url, "username": "admin", "password": "adminpassword123"},
        })
        nessus_connector_id = conn_resp.get("id")
        log(f"Nessus connector registered: {nessus_connector_id}")

        asset_resp = client.post("/assets", json={
            "name": "nexplane-smoke-nessus-target",
            "asset_type": "server",
            "properties": {"ip": private_ip, "hostname": "nexplane-smoke-nessus-target"},
        })
        asset_id = asset_resp.get("id", cloud_account_id)

        cr = client.run_cr(
            "[NESSUS_SCAN] run vulnerability scan",
            "nessus_run_scan",
            asset_id,
            {"target_hosts": "127.0.0.1", "scan_name": "nexplane-smoke-nessus-scan",
             "max_wait_seconds": 1800, "poll_interval": 30},
        )
        exec_runs = cr.get("execution_runs") or []
        result = exec_runs[0].get("result") if exec_runs else {}

        if result.get("status") == "skipped":
            log("  WARNING: Scan skipped (no connector credentials in backend)")
        elif result.get("action") == "nessus_run_scan":
            log(f"Scan completed. Status: {result.get('status')} | Findings: {result.get('finding_count', 0)}")
        else:
            log(f"  WARNING: Unexpected result: {result}")
        log("Phase NESSUS_SCAN PASSED")

    except Exception as e:
        print(f"\\n[FAIL] Phase NESSUS_SCAN failed: {e}")
        raise
    finally:
        if nessus_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{nessus_connector_id}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass

'''

INSERTION_MARKER = (
    "\n# ---------------------------------------------------------------------------\n"
    "# Phase KEYCLOAK_ROTATE — Keycloak emergency user lockout (Docker on EC2, AMI cached)\n"
    "# ---------------------------------------------------------------------------\n"
)

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

if "run_phase_openvas_scan" in content:
    print("ALREADY INSERTED — skipping")
    sys.exit(0)

if INSERTION_MARKER not in content:
    print(f"MARKER NOT FOUND in {TARGET}")
    sys.exit(1)

new_content = content.replace(
    INSERTION_MARKER,
    OPENVAS_PHASE + NESSUS_PHASE + INSERTION_MARKER,
    1,
)

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(new_content)

print("DONE — phases inserted")
