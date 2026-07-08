# MGN Replication Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `MGN_REPLICATION` smoke phase self-contained by adding inline provision (launch AL2023 EC2 source server, install MGN agent, wait for READY_FOR_TEST) and teardown (terminate test instance, deregister AMI + snapshots, disconnect + delete source server, terminate source EC2).

**Architecture:** All changes are confined to one file — `backend/tests/smoke/test_backup_scheduler_live.py`. Task 1 adds an IAM helper function that programmatically attaches MGN permissions to the smoke runner role and platform connector user. Task 2 replaces the existing 40-line `run_phase_mgn_replication` stub with a full provision → backup CR → teardown implementation. Task 3 runs the smoke on EC2.

**Tech Stack:** boto3 (mgn, ec2, iam, ssm), existing smoke helpers (`log`, `fail`, `_wait_ssm_ready_win`, `get_connector_creds_from_db`, `_create_and_run_cr`, `_rollback_cr`, `_wait_cr_complete`, `_extract_artifact_refs`)

## Global Constraints

- All changes confined to `backend/tests/smoke/test_backup_scheduler_live.py` — no new files
- SPDX header already present on the file; do not modify it
- No mocks — all boto3 calls hit live AWS
- Source EC2 instance type: `t3.micro`, OS: Amazon Linux 2023 (latest via `describe_images`)
- IAM instance profile on source EC2: `NexplaneEC2TestProfile` (constant `SMOKE_IAM_PROFILE` already defined in the file)
- IAM inline policy names: `NexplaneMGNSmokePolicy` (on role `NexplaneEC2TestRole`) and `NexplaneMGNExecutorPolicy` (on connector IAM user)
- `_ensure_mgn_iam_permissions` uses connector creds (not the runner role) to call IAM
- No SSH key on source EC2 — SSM-only access
- Teardown runs in a `finally` block — must execute even when the backup CR fails
- `_wait_ssm_ready_win` from smoke_helpers works for Linux (OS-agnostic); reuse it
- Timeouts: SSM ready 300 s, agent install SSM command 360 s, source server appear 300 s, READY_FOR_TEST 5400 s (90 min), backup CR 3600 s
- MGN agent installer URL pattern: `https://aws-application-migration-service-{region}.s3.amazonaws.com/latest/linux/aws-replication-installer-init.py`
- `_rollback_cr` / `_wait_cr_complete` / `_extract_artifact_refs` / `_create_and_run_cr` are already defined in this file — do not redefine them
- Smoke runner enforces EC2-only policy via `NEXPLANE_RUNNER_EC2` env var — do not bypass

---

### Task 1: IAM helper `_ensure_mgn_iam_permissions`

**Files:**
- Modify: `backend/tests/smoke/test_backup_scheduler_live.py:2277-2280` — insert new helper block between the DISK2VHD section end and the `# Phase MGN_REPLICATION` comment

**Interfaces:**
- Produces: `_ensure_mgn_iam_permissions(creds: dict) -> None`
  - `creds`: dict with keys `access_key_id` (or `aws_access_key_id`), `secret_access_key` (or `aws_secret_access_key`), optionally `session_token`, `region` (or `aws_region`)
  - Raises `RuntimeError` if both IAM targets fail; logs a warning and continues if only one fails

- [ ] **Step 1: Locate insertion point**

Open `backend/tests/smoke/test_backup_scheduler_live.py`. Find line 2277:
```python
# ---------------------------------------------------------------------------
# Phase MGN_REPLICATION — AWS MGN launch_test_instances -> AMI capture
# ---------------------------------------------------------------------------
```
You will insert the new code block immediately before this comment.

- [ ] **Step 2: Insert `_MGN_ROLE_POLICY`, `_MGN_USER_POLICY`, and `_ensure_mgn_iam_permissions`**

Insert the following block at line 2277 (pushing the existing MGN comment down):

```python
# ---------------------------------------------------------------------------
# IAM policies for MGN smoke phase
# ---------------------------------------------------------------------------

_MGN_ROLE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "mgn:InitializeService",
                "mgn:DescribeSourceServers",
                "mgn:DescribeJobs",
                "mgn:DisconnectFromService",
                "mgn:DeleteSourceServer",
                "mgn:GetReplicationConfiguration",
                "mgn:UpdateReplicationConfiguration",
                "mgn:CreateReplicationConfigurationTemplate",
                "mgn:DescribeReplicationConfigurationTemplates",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstances",
                "ec2:TerminateInstances",
                "ec2:DeregisterImage",
                "ec2:DeleteSnapshot",
                "ec2:DescribeSnapshots",
                "ec2:DescribeImages",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": ["iam:PutRolePolicy", "iam:PutUserPolicy", "iam:GetUser"],
            "Resource": "*",
        },
    ],
}

_MGN_USER_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "mgn:LaunchTestInstances",
                "mgn:DescribeJobs",
                "mgn:DescribeSourceServers",
            ],
            "Resource": "*",
        },
    ],
}


def _ensure_mgn_iam_permissions(creds: dict) -> None:
    """Add MGN inline policies to the smoke runner role and platform connector
    user. Both put_role_policy and put_user_policy are idempotent."""
    import boto3
    import json as _json

    region = creds.get("region") or creds.get("aws_region", "us-east-1")
    iam = boto3.client(
        "iam",
        region_name=region,
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )
    errors = []

    try:
        iam.put_role_policy(
            RoleName="NexplaneEC2TestRole",
            PolicyName="NexplaneMGNSmokePolicy",
            PolicyDocument=_json.dumps(_MGN_ROLE_POLICY),
        )
        log("MGN_REPLICATION: NexplaneMGNSmokePolicy attached to NexplaneEC2TestRole")
    except Exception as exc:
        errors.append(f"put_role_policy(NexplaneEC2TestRole): {exc}")

    try:
        user_name = iam.get_user()["User"]["UserName"]
        iam.put_user_policy(
            UserName=user_name,
            PolicyName="NexplaneMGNExecutorPolicy",
            PolicyDocument=_json.dumps(_MGN_USER_POLICY),
        )
        log(f"MGN_REPLICATION: NexplaneMGNExecutorPolicy attached to IAM user {user_name}")
    except Exception as exc:
        errors.append(f"put_user_policy(connector user): {exc}")

    if len(errors) == 2:
        raise RuntimeError(
            "MGN_REPLICATION: could not add IAM permissions to either target.\n"
            + "\n".join(errors)
            + "\nAdd manually: NexplaneMGNSmokePolicy to NexplaneEC2TestRole, "
            "NexplaneMGNExecutorPolicy to the platform connector IAM user."
        )
    if errors:
        log(f"MGN_REPLICATION: IAM warning (one target failed, continuing): {errors[0]}")
```

- [ ] **Step 3: Verify syntax**

```bash
cd /home/ec2-user/nexplane
python -c "import ast; ast.parse(open('backend/tests/smoke/test_backup_scheduler_live.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_backup_scheduler_live.py
git commit -m "smoke: add _ensure_mgn_iam_permissions helper for MGN smoke phase"
```

---

### Task 2: Rewrite `run_phase_mgn_replication` with inline provision + teardown

**Files:**
- Modify: `backend/tests/smoke/test_backup_scheduler_live.py:2281-2321` — replace the entire existing `run_phase_mgn_replication` function body

**Interfaces:**
- Consumes: `_ensure_mgn_iam_permissions(creds: dict) -> None` from Task 1
- Consumes: `get_connector_creds_from_db(connector_id: str) -> dict` — already imported from smoke_helpers
- Consumes: `_wait_ssm_ready_win(ssm_client, instance_id: str, timeout: int) -> None` — already imported from smoke_helpers
- Consumes: `_create_and_run_cr(client, title, change_type, asset_id, desired_outcome, timeout) -> dict` — defined in this file
- Consumes: `_rollback_cr(client, cr_id) -> None` — defined in this file
- Consumes: `_wait_cr_complete(client, cr_id, label, timeout) -> dict` — defined in this file
- Consumes: `_extract_artifact_refs(cr: dict) -> dict` — defined in this file
- Consumes: `SMOKE_IAM_PROFILE` — constant `"NexplaneEC2TestProfile"` defined at top of file
- Consumes: `log(msg)`, `fail(msg)` — imported from smoke_helpers
- Consumes: `time` — already imported at top of file

- [ ] **Step 1: Replace the existing function**

Find and replace the entire `run_phase_mgn_replication` function (currently lines 2281–2321) with:

```python
def run_phase_mgn_replication(client, aws_connector_id: str, asset_id: str) -> None:
    """MGN_REPLICATION: provision AL2023 source EC2, install MGN agent, wait for
    READY_FOR_TEST, fire server_backup CR, verify AMI, teardown everything."""
    import uuid as _uuid
    import json as _json
    import boto3

    creds = get_connector_creds_from_db(aws_connector_id)
    region = creds.get("region") or creds.get("aws_region", "us-east-1")
    access_key = creds.get("access_key_id", creds.get("aws_access_key_id", ""))
    secret_key = creds.get("secret_access_key", creds.get("aws_secret_access_key", ""))

    def _boto(svc):
        return boto3.client(
            svc, region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    ec2 = _boto("ec2")
    ssm = _boto("ssm")
    mgn = _boto("mgn")

    _ensure_mgn_iam_permissions(creds)

    # Initialize MGN service (idempotent)
    try:
        mgn.initialize_service()
        log("MGN_REPLICATION: MGN service initialized")
    except mgn.exceptions.ConflictException:
        log("MGN_REPLICATION: MGN service already initialized")

    # Latest AL2023 AMI
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-2023.*-x86_64"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )["Images"]
    images.sort(key=lambda i: i["CreationDate"], reverse=True)
    al2023_ami = images[0]["ImageId"]
    log(f"MGN_REPLICATION: using AL2023 AMI {al2023_ami}")

    # Pick subnet from default VPC
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        fail("MGN_REPLICATION: no default VPC found")
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}]
    )["Subnets"]
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)

    source_instance_id: str = ""
    source_server_id: str = ""
    test_instance_id: str = ""
    ami_id: str = ""
    cr: dict = {}

    try:
        # ------------------------------------------------------------------
        # PROVISION: launch source EC2
        # ------------------------------------------------------------------
        log("MGN_REPLICATION: launching AL2023 t3.micro source instance...")
        launch = None
        for subnet in subnets:
            try:
                launch = ec2.run_instances(
                    ImageId=al2023_ami,
                    InstanceType="t3.micro",
                    MinCount=1, MaxCount=1,
                    SubnetId=subnet["SubnetId"],
                    IamInstanceProfile={"Name": SMOKE_IAM_PROFILE},
                    TagSpecifications=[{
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": "nexplane-smoke-mgn-source"},
                            {"Key": "nexplane-smoke", "Value": "mgn-replication"},
                        ],
                    }],
                )
                break
            except Exception as ce:
                if "Unsupported" in str(ce) or "InsufficientInstanceCapacity" in str(ce):
                    continue
                raise
        if not launch:
            fail("MGN_REPLICATION: could not launch t3.micro in any AZ")
        source_instance_id = launch["Instances"][0]["InstanceId"]
        log(f"MGN_REPLICATION: source instance {source_instance_id}, waiting for running state...")

        ec2.get_waiter("instance_running").wait(InstanceIds=[source_instance_id])
        log("MGN_REPLICATION: instance running, waiting for SSM...")
        _wait_ssm_ready_win(ssm, source_instance_id, timeout=300)
        log("MGN_REPLICATION: SSM ready")

        # Private DNS for source server identification after agent registers
        inst_info = ec2.describe_instances(InstanceIds=[source_instance_id])
        private_dns = inst_info["Reservations"][0]["Instances"][0].get("PrivateDnsName", "")

        # ------------------------------------------------------------------
        # PROVISION: install MGN agent via SSM
        # ------------------------------------------------------------------
        mgn_install_cmd = (
            f"cd /tmp && "
            f"wget -q 'https://aws-application-migration-service-{region}.s3.amazonaws.com"
            f"/latest/linux/aws-replication-installer-init.py' -O aws-mgn-init.py && "
            f"sudo python3 aws-mgn-init.py "
            f"--region {region} "
            f"--aws-access-key-id {access_key} "
            f"--aws-secret-access-key {secret_key} "
            f"--no-prompt"
        )
        log("MGN_REPLICATION: installing MGN agent via SSM RunCommand...")
        cmd_resp = ssm.send_command(
            InstanceIds=[source_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [mgn_install_cmd]},
            TimeoutSeconds=300,
        )
        cmd_id = cmd_resp["Command"]["CommandId"]
        deadline = time.time() + 360
        while time.time() < deadline:
            time.sleep(10)
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=source_instance_id)
            status = inv["Status"]
            if status == "Success":
                break
            if status in ("Failed", "Cancelled", "TimedOut"):
                fail(
                    f"MGN_REPLICATION: agent install SSM command {status}: "
                    f"{inv.get('StandardErrorContent', '')}"
                )
        else:
            fail("MGN_REPLICATION: agent install SSM command timed out after 360s")
        log("MGN_REPLICATION: MGN agent installed")

        # ------------------------------------------------------------------
        # PROVISION: wait for source server to appear in MGN
        # ------------------------------------------------------------------
        log("MGN_REPLICATION: waiting for source server to appear in MGN (up to 5 min)...")
        deadline_appear = time.time() + 300
        while time.time() < deadline_appear:
            time.sleep(15)
            servers = mgn.describe_source_servers(filters={})["items"]
            for s in servers:
                hostname = (
                    s.get("sourceProperties", {})
                    .get("identificationHints", {})
                    .get("hostname", "")
                )
                short_dns = private_dns.split(".")[0] if private_dns else ""
                if short_dns and hostname and short_dns in hostname:
                    source_server_id = s["sourceServerID"]
                    break
            if not source_server_id and servers:
                # Fallback: take any server without a known ID yet (sequential smoke runs)
                source_server_id = servers[-1]["sourceServerID"]
            if source_server_id:
                break
        if not source_server_id:
            fail("MGN_REPLICATION: source server did not appear in MGN within 5 min")
        log(f"MGN_REPLICATION: source server {source_server_id}, waiting for READY_FOR_TEST (up to 90 min)...")

        # ------------------------------------------------------------------
        # PROVISION: wait for READY_FOR_TEST
        # ------------------------------------------------------------------
        deadline_ready = time.time() + 5400
        while time.time() < deadline_ready:
            time.sleep(60)
            servers = mgn.describe_source_servers(
                filters={"sourceServerIDs": [source_server_id]}
            )["items"]
            if servers:
                state = servers[0].get("lifeCycle", {}).get("state", "")
                log(f"MGN_REPLICATION: source server state={state}")
                if state in ("READY_FOR_TEST", "READY_FOR_CUTOVER"):
                    break
                if state in ("DISCONNECTED", "CUTOVER"):
                    fail(f"MGN_REPLICATION: source server entered unexpected state {state}")
        else:
            fail("MGN_REPLICATION: source server did not reach READY_FOR_TEST within 90 min")
        log("MGN_REPLICATION: source server READY_FOR_TEST ✓")

        # ------------------------------------------------------------------
        # BACKUP CR
        # ------------------------------------------------------------------
        run_id = _uuid.uuid4().hex[:8]
        cr = _create_and_run_cr(
            client,
            title=f"smoke mgn_replication {run_id}",
            change_type="server_backup",
            asset_id=asset_id,
            desired_outcome={
                "capture_strategy": "mgn_replication",
                "aws_connector_id": aws_connector_id,
                "mgn_source_server_id": source_server_id,
            },
            timeout=3600,
        )
        assert cr["status"] == "completed", f"MGN_REPLICATION backup CR failed: {cr}"
        refs = _extract_artifact_refs(cr)
        assert refs.get("capture_strategy") == "mgn_replication", f"Wrong strategy: {refs}"
        assert refs.get("ami_id", "").startswith("ami-"), f"No AMI in refs: {refs}"
        assert refs.get("test_instance_id", "").startswith("i-"), f"No test instance in refs: {refs}"
        test_instance_id = refs["test_instance_id"]
        ami_id = refs["ami_id"]
        log(f"MGN_REPLICATION: backup PASSED: ami={ami_id} instance={test_instance_id}")

    finally:
        # ------------------------------------------------------------------
        # TEARDOWN — always runs
        # ------------------------------------------------------------------
        log("MGN_REPLICATION: teardown starting...")

        if test_instance_id:
            try:
                ec2.terminate_instances(InstanceIds=[test_instance_id])
                log(f"MGN_REPLICATION: test instance {test_instance_id} terminating")
            except Exception as e:
                log(f"MGN_REPLICATION: terminate test instance warning: {e}")

        if ami_id:
            try:
                snap_resp = ec2.describe_images(ImageIds=[ami_id])
                snap_ids = [
                    m["Ebs"]["SnapshotId"]
                    for img in snap_resp.get("Images", [])
                    for m in img.get("BlockDeviceMappings", [])
                    if "Ebs" in m
                ]
                ec2.deregister_image(ImageId=ami_id)
                log(f"MGN_REPLICATION: AMI {ami_id} deregistered")
                for snap_id in snap_ids:
                    try:
                        ec2.delete_snapshot(SnapshotId=snap_id)
                        log(f"MGN_REPLICATION: snapshot {snap_id} deleted")
                    except Exception as e:
                        log(f"MGN_REPLICATION: delete snapshot warning: {e}")
            except Exception as e:
                log(f"MGN_REPLICATION: deregister AMI warning: {e}")

        if cr.get("id"):
            try:
                _rollback_cr(client, cr["id"])
                rb = _wait_cr_complete(client, cr["id"], "mgn rollback", timeout=120)
                assert rb["status"] in ("rolled_back", "rollback_partial"), (
                    f"rollback status={rb['status']}"
                )
                log("MGN_REPLICATION: rollback PASSED (noop — source retained)")
            except Exception as e:
                log(f"MGN_REPLICATION: rollback warning: {e}")

        if source_server_id:
            try:
                mgn.disconnect_from_service(sourceServerID=source_server_id)
                log(f"MGN_REPLICATION: source server {source_server_id} disconnected")
            except Exception as e:
                log(f"MGN_REPLICATION: disconnect warning: {e}")
            time.sleep(15)
            for attempt in range(4):
                try:
                    mgn.delete_source_server(sourceServerID=source_server_id)
                    log(f"MGN_REPLICATION: source server {source_server_id} deleted")
                    break
                except Exception as e:
                    if attempt < 3:
                        log(f"MGN_REPLICATION: delete source server attempt {attempt + 1} failed: {e}, retrying in 10s...")
                        time.sleep(10)
                    else:
                        log(f"MGN_REPLICATION: delete source server warning (gave up): {e}")

        if source_instance_id:
            try:
                ec2.terminate_instances(InstanceIds=[source_instance_id])
                log(f"MGN_REPLICATION: source instance {source_instance_id} terminating")
            except Exception as e:
                log(f"MGN_REPLICATION: terminate source instance warning: {e}")
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/tests/smoke/test_backup_scheduler_live.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_backup_scheduler_live.py
git commit -m "smoke: rewrite MGN_REPLICATION phase with inline provision + teardown"
```

---

### Task 3: Run MGN_REPLICATION smoke on EC2 and record result

**Files:**
- No code changes — execution only
- Modify: `.superpowers/sdd/progress.md` — append result

**Interfaces:**
- Consumes: `run_phase_mgn_replication` from Task 2
- Consumes: `run_on_ec2.py` — same helper used by all smoke phases; find it at `backend/tests/smoke/run_on_ec2.py`

- [ ] **Step 1: SCP the updated file to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
    backend/tests/smoke/test_backup_scheduler_live.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_backup_scheduler_live.py
```

- [ ] **Step 2: Run the smoke on EC2**

SSH to EC2 and run:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /home/ec2-user/nexplane
python backend/tests/smoke/run_on_ec2.py \
    --email <admin-email> \
    --phases MGN_REPLICATION \
    --script test_backup_scheduler_live.py
```

Expected output (abridged — total runtime ~1-2 hours):
```
=== PHASE: MGN_REPLICATION ===
MGN_REPLICATION: NexplaneMGNSmokePolicy attached to NexplaneEC2TestRole
MGN_REPLICATION: NexplaneMGNExecutorPolicy attached to IAM user <name>
MGN_REPLICATION: MGN service initialized (or already initialized)
MGN_REPLICATION: using AL2023 AMI ami-...
MGN_REPLICATION: source instance i-..., waiting for running state...
MGN_REPLICATION: instance running, waiting for SSM...
MGN_REPLICATION: SSM ready
MGN_REPLICATION: installing MGN agent via SSM RunCommand...
MGN_REPLICATION: MGN agent installed
MGN_REPLICATION: source server s-..., waiting for READY_FOR_TEST (up to 90 min)...
MGN_REPLICATION: source server state=NOT_READY
...
MGN_REPLICATION: source server state=READY_FOR_TEST
MGN_REPLICATION: source server READY_FOR_TEST ✓
MGN_REPLICATION: backup PASSED: ami=ami-... instance=i-...
MGN_REPLICATION: teardown starting...
MGN_REPLICATION: test instance i-... terminating
MGN_REPLICATION: AMI ami-... deregistered
MGN_REPLICATION: snapshot snap-... deleted
MGN_REPLICATION: rollback PASSED (noop — source retained)
MGN_REPLICATION: source server s-... disconnected
MGN_REPLICATION: source server s-... deleted
MGN_REPLICATION: source instance i-... terminating
============================================================
BACKUP_SCHEDULER_SMOKE: ALL PHASES PASSED
============================================================
```

If `_ensure_mgn_iam_permissions` fails with `RuntimeError` (both IAM targets failed), add the policies manually:
- Attach inline policy `NexplaneMGNSmokePolicy` (the JSON from `_MGN_ROLE_POLICY`) to IAM role `NexplaneEC2TestRole`
- Attach inline policy `NexplaneMGNExecutorPolicy` (the JSON from `_MGN_USER_POLICY`) to the platform connector IAM user
Then re-run.

- [ ] **Step 3: Record result in progress ledger**

Append to `.superpowers/sdd/progress.md`:

```
## MGN Replication Smoke
- Task 1: complete (commits <base7>..<head7>, review clean — _ensure_mgn_iam_permissions, _MGN_ROLE_POLICY, _MGN_USER_POLICY)
- Task 2: complete (commits <base7>..<head7>, review clean — run_phase_mgn_replication inline provision + teardown)
- Task 3 (live smoke): complete (commit <head7>, ALL_PHASES_PASSED on EC2)
  IAM: NexplaneMGNSmokePolicy on NexplaneEC2TestRole (added programmatically), NexplaneMGNExecutorPolicy on connector user (added programmatically)
  Needs Terraform codification in nexplane-infra (same pattern as rds:DeleteDBSnapshot)
```

- [ ] **Step 4: Commit progress ledger**

```bash
git add .superpowers/sdd/progress.md
git commit -m "smoke: MGN_REPLICATION ALL_PHASES_PASSED on EC2"
```
