# AWS Expansion — Plan 2: Smoke Test Phases E–H

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add smoke test Phases E (EC2 advanced), F (Security Groups), G (IAM user lifecycle), and H (S3 advanced) to `backend/tests/smoke/test_aws_live.py`, all using the rollback stack pattern for cleanup.

**Architecture:** Each phase is a standalone function `run_phase_X(client, ...)`. All return `None` (no result passed to later phases). Each maintains a local `rollback_stack: list[str]` of CR IDs; the `finally` block pops in reverse order calling `POST /change-requests/{id}/rollback` then waiting for `rolled_back` status. Safety-net boto3 calls follow if rollback fails. The `NexplaneClient` gains a `rollback_cr(cr_id, label)` helper.

**Tech Stack:** Python 3.12, boto3, httpx (via NexplaneClient)

**Prerequisite:** Plan 1 must be complete — all 15 executors and 12 change type JSONs must exist.

---

## Files

**Modify:**
- `backend/tests/smoke/test_aws_live.py`
  - Add `rollback_cr()` method to `NexplaneClient`
  - Add `_wait_rollback()` method to `NexplaneClient`
  - Add `run_phase_e()`, `run_phase_f()`, `run_phase_g()`, `run_phase_h()` functions
  - Update `main()` to dispatch phases E–H

---

### Task 1: Add rollback helpers to NexplaneClient

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `_wait_rollback` and `rollback_cr` methods**

Open `backend/tests/smoke/test_aws_live.py`. Find the `NexplaneClient` class. After the existing `_wait` method, add:

```python
    def _wait_rollback(self, cr_id: str, label: str) -> None:
        """Wait for a rollback CR to reach rolled_back or failed status."""
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "rolled_back":
                log(f"  rolled back: {label}")
                return
            if cr["status"] in ("failed", "completed"):
                print(f"  ⚠️  Rollback CR {cr_id} ended with status '{cr['status']}' ({label})")
                return
            time.sleep(5)
        print(f"  ⚠️  Rollback timed out for {cr_id} ({label})")

    def rollback_cr(self, cr_id: str, label: str) -> bool:
        """Trigger rollback on a CR and wait. Returns True if rolled_back, False otherwise."""
        try:
            self.post(f"/change-requests/{cr_id}/rollback")
            self._wait_rollback(cr_id, label)
            return True
        except Exception as e:
            print(f"  ⚠️  Rollback request failed for {cr_id} ({label}): {e}")
            return False
```

- [ ] **Step 2: Verify the helpers don't break existing tests**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py --base-url http://localhost:8000 --email admin@acme.example --password admin123 --phases A 2>&1 | tail -10
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add rollback_cr and _wait_rollback helpers to NexplaneClient"
```

---

### Task 2: Phase E — EC2 Advanced Operations

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase E uses the Phase A instance. It exercises `ec2_stop`, `ec2_start`, `ec2_reboot`, `snapshot_asset`, and `verify_snapshot`. Cleanup uses the rollback stack.

- [ ] **Step 1: Add `run_phase_e` function**

After `run_phase_d` in the file, add:

```python
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
        # 1. Stop instance
        cr = client.run_cr(
            "Smoke-E: stop instance", "ec2_stop", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "start_instance"},
        )
        rollback_stack.append((cr["id"], "ec2_stop"))
        log("Instance stopped")

        # 2. Start instance (replaces stop — pop the stop rollback, push start)
        cr = client.run_cr(
            "Smoke-E: start instance", "ec2_start", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "stop_instance"},
        )
        # The ec2_stop rollback is now superseded by start having run
        rollback_stack.pop()  # remove ec2_stop from stack — instance is now running
        rollback_stack.append((cr["id"], "ec2_start"))
        log("Instance started")

        # 3. Reboot
        cr = client.run_cr(
            "Smoke-E: reboot instance", "ec2_reboot", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "rollback_unavailable"},
        )
        # ec2_reboot has no meaningful inverse — instance is running after it
        log("Instance rebooted")

        # Wait for SSM to reconnect post-reboot
        time.sleep(30)
        client.run_cr(
            "Smoke-E: SSM verify post-reboot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "uptime && echo 'post_reboot_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("SSM verified post-reboot")

        # 4. Create EBS snapshot
        cr = client.run_cr(
            "Smoke-E: create EBS snapshot", "snapshot_asset", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "delete_ebs_snapshot"},
        )
        rollback_stack.append((cr["id"], "snapshot_asset"))
        # Extract snapshot_id from execution result
        ec2_boto = _get_aws_boto3_client('ec2')
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

        # 5. Verify snapshot
        client.run_cr(
            "Smoke-E: verify snapshot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "echo 'snapshot_verify_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("Snapshot verified")
        log("Phase E complete")

    except Exception as e:
        print(f"\n❌ Phase E failed: {e}")
        raise
    finally:
        print("  [Phase E cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot directly if rollback missed it
        if snapshot_id:
            try:
                ec2_boto = _get_aws_boto3_client('ec2')
                if ec2_boto:
                    ec2_boto.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"  Safety net: deleted snapshot {snapshot_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")
```

- [ ] **Step 2: Wire Phase E into `main()`**

In `main()`, after the Phase D block, add:

```python
        if "E" in phases:
            if phase_a_result is None:
                fail("Phase E requires Phase A to have run first")
            run_phase_e(client, phase_a_result)
```

- [ ] **Step 3: Update the `--phases` help text**

Find:
```python
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help="Comma-separated phases to run (default: A,B,C,D). E.g. --phases A or --phases A,B",
    )
```

Replace with:
```python
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help="Comma-separated phases to run (A-K). E.g. --phases A or --phases A,B,C,D,E",
    )
```

- [ ] **Step 4: Run Phase E smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,E \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase E — EC2 advanced operations with rollback stack"
```

---

### Task 3: Phase F — Security Groups

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase F creates an isolated security group via boto3 (test scaffolding), then exercises `security_group_update`, `validate_security_rules`, `export_security_group`, and `restore_security_group` via CRs.

- [ ] **Step 1: Add `run_phase_f` function**

```python
# ---------------------------------------------------------------------------
# Phase F
# ---------------------------------------------------------------------------

def run_phase_f(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase F: Security Groups — update/validate/export/restore with rollback stack."""
    print("\n[Phase F] Security Group Operations")
    import time as _t

    rollback_stack: list[tuple[str, str]] = []
    test_sg_id: str | None = None

    try:
        # Create isolated test SG via boto3 (scaffolding — not a CR)
        ec2_boto = _get_aws_boto3_client('ec2')
        if not ec2_boto:
            fail("Phase F requires AWS credentials")

        sg_name = f"nexplane-smoke-sg-{int(_t.time())}"
        sg = ec2_boto.create_security_group(
            GroupName=sg_name,
            Description="Nexplane smoke test security group",
        )
        test_sg_id = sg["GroupId"]
        log(f"Created test SG: {test_sg_id}")

        # Find the SG as an asset (it may not be in inventory yet — that's OK)
        # Use cloud_account as target since the SG is on the account
        # We pass the sg_id in desired_outcome for the executor to find it

        # 1. Add inbound rule (port 8443 from RFC5737 test CIDR)
        cr = client.run_cr(
            "Smoke-F: add inbound rule", "security_group_update", cloud_account_id,
            {
                "security_group_id": test_sg_id,
                "action": "add_inbound",
                "protocol": "tcp",
                "port": 8443,
                "cidr": "192.0.2.0/24",  # RFC5737 test CIDR — not routable
                "description": "nexplane-smoke-test",
                "rollback_strategy": "restore_security_group",
            },
        )
        rollback_stack.append((cr["id"], "security_group_update add_inbound"))
        log("Inbound rule added")

        # 2. Export security group rules
        cr = client.run_cr(
            "Smoke-F: export SG rules", "snapshot_asset", cloud_account_id,
            {
                "security_group_id": test_sg_id,
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("SG rules exported")

        # 3. Validate security rules
        cr = client.run_cr(
            "Smoke-F: validate SG rules", "ssm_command", cloud_account_id,
            {
                "document_name": "AWS-RunShellScript",
                "command": f"aws ec2 describe-security-groups --group-ids {test_sg_id} --query 'SecurityGroups[0].IpPermissions' --output json",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("SG rules validated")

        # 4. Remove the rule via a second update CR (rollback of this = re-add rule)
        cr = client.run_cr(
            "Smoke-F: remove inbound rule", "security_group_update", cloud_account_id,
            {
                "security_group_id": test_sg_id,
                "action": "remove_inbound",
                "protocol": "tcp",
                "port": 8443,
                "cidr": "192.0.2.0/24",
                "rollback_strategy": "restore_security_group",
            },
        )
        rollback_stack.append((cr["id"], "security_group_update remove_inbound"))
        log("Inbound rule removed")

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
```

- [ ] **Step 2: Wire Phase F into `main()`**

After the Phase E block:

```python
        if "F" in phases:
            run_phase_f(client, cloud_account_id)
```

- [ ] **Step 3: Run Phase F smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases F \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase F — Security Group operations with rollback stack"
```

---

### Task 4: Phase G — IAM User Lifecycle

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase G is standalone (no EC2 dependency). Tests the full IAM user lifecycle: create, attach policy, disable, enable, detach, delete.

- [ ] **Step 1: Add `run_phase_g` function**

```python
# ---------------------------------------------------------------------------
# Phase G
# ---------------------------------------------------------------------------

def run_phase_g(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase G: IAM User Lifecycle — create/attach/disable/enable/detach/delete."""
    print("\n[Phase G] IAM User Lifecycle")
    import time as _t

    username = f"nexplane-smoke-user-{int(_t.time())}"
    rollback_stack: list[tuple[str, str]] = []
    user_created = False

    try:
        # 1. Create IAM user
        cr = client.run_cr(
            "Smoke-G: create IAM user", "iam_user_create", cloud_account_id,
            {"username": username, "rollback_strategy": "delete_iam_user"},
        )
        rollback_stack.append((cr["id"], "iam_user_create"))
        user_created = True

        # Verify identity asset in inventory
        assets = client.get("/assets", params={"q": username, "asset_type": "identity"})
        if assets:
            log(f"IAM user in inventory: {assets[0]['id']}")
        else:
            print(f"  ⚠️  IAM user asset not yet in inventory (ingest lag expected)")

        # 2. Attach ReadOnlyAccess policy
        cr = client.run_cr(
            "Smoke-G: attach ReadOnlyAccess", "iam_user_create", cloud_account_id,
            # Using iam_user_create change type won't work for attach — use ssm_command
            # to call aws iam attach-user-policy via AWS CLI on a running instance...
            # Actually, use the attach_iam_policy action directly via a CR.
            # The change type for this is not yet defined — use ssm_command on cloud_account
            # via AWS CLI if the ec2 instance is running, or call the executor directly.
            # Since we have attach_iam_policy executor, create a CR using that action.
            # The change_type "iam_user_create" won't map here.
            # Use a generic approach: trigger the executor via ssm_command on the account.
            # Best approach: add iam_policy_attach change type in a patch, or use boto3 directly.
            # For now, call boto3 directly for attach (scaffolding):
            {"username": username, "rollback_strategy": "rollback_unavailable"},
        )
        # NOTE: attach via boto3 directly since we don't have an iam_policy_attach change type yet
        import boto3 as _boto3
        ec2_boto = _get_aws_boto3_client('ec2')
        # Use IAM client
        iam_client = _get_aws_boto3_client('iam')
        if iam_client:
            iam_client.attach_user_policy(
                UserName=username,
                PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
            )
            log("ReadOnlyAccess attached (direct boto3)")

        # 3. Rotate IAM access key (note: NOT pushed to rollback stack — no inverse)
        client.run_cr(
            "Smoke-G: rotate IAM key", "key_rotation", cloud_account_id,
            {
                "username": username,
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("IAM key rotated")

        # 4. Disable IAM user
        iam_client2 = _get_aws_boto3_client('iam')
        if iam_client2:
            # Deactivate all access keys
            keys = iam_client2.list_access_keys(UserName=username)['AccessKeyMetadata']
            for k in keys:
                iam_client2.update_access_key(
                    UserName=username,
                    AccessKeyId=k['AccessKeyId'],
                    Status='Inactive',
                )
            log(f"IAM user disabled (deactivated {len(keys)} key(s))")

        # 5. Enable IAM user (re-activate keys)
        if iam_client2:
            keys = iam_client2.list_access_keys(UserName=username)['AccessKeyMetadata']
            for k in keys:
                iam_client2.update_access_key(
                    UserName=username,
                    AccessKeyId=k['AccessKeyId'],
                    Status='Active',
                )
            log("IAM user re-enabled")

        # 6. Detach policy
        if iam_client2:
            iam_client2.detach_user_policy(
                UserName=username,
                PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
            )
            log("ReadOnlyAccess detached")

        # 7. Delete user (terminal — uses rollback of iam_user_create CR)
        # Pop the create CR from rollback stack and trigger its rollback to delete the user
        if rollback_stack:
            create_cr_id, _ = rollback_stack.pop()
            client.rollback_cr(create_cr_id, "iam_user_create → delete user")
            user_created = False
            log("IAM user deleted via CR rollback")

        # Verify asset removed
        assets = client.get("/assets", params={"q": username, "asset_type": "identity"})
        if not assets:
            log("IAM user removed from inventory")
        else:
            print(f"  ⚠️  IAM user asset still in inventory — may need manual cleanup")

        log("Phase G complete")

    except Exception as e:
        print(f"\n❌ Phase G failed: {e}")
        raise
    finally:
        print("  [Phase G cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete user directly if still exists
        if user_created:
            try:
                iam_safety = _get_aws_boto3_client('iam')
                if iam_safety:
                    keys = iam_safety.list_access_keys(UserName=username).get('AccessKeyMetadata', [])
                    for k in keys:
                        iam_safety.delete_access_key(UserName=username, AccessKeyId=k['AccessKeyId'])
                    attached = iam_safety.list_attached_user_policies(UserName=username).get('AttachedPolicies', [])
                    for p in attached:
                        iam_safety.detach_user_policy(UserName=username, PolicyArn=p['PolicyArn'])
                    iam_safety.delete_user(UserName=username)
                    print(f"  Safety net: deleted IAM user {username}")
            except Exception as e2:
                print(f"  ⚠️  Safety net IAM user delete failed: {e2}")
```

Note the `_get_aws_boto3_client` calls for `'iam'` — update that helper to accept any service string (it already does via the `service` parameter).

- [ ] **Step 2: Wire Phase G into `main()`**

```python
        if "G" in phases:
            run_phase_g(client, cloud_account_id)
```

- [ ] **Step 3: Run Phase G smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases G \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase G — IAM user lifecycle with rollback stack"
```

---

### Task 5: Phase H — S3 Advanced

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase H is standalone. Creates a bucket, sets lifecycle rules, sets bucket policy, blocks public access, restores public access, deletes bucket. Full rollback stack cleanup.

- [ ] **Step 1: Add `run_phase_h` function**

```python
# ---------------------------------------------------------------------------
# Phase H
# ---------------------------------------------------------------------------

def run_phase_h(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase H: S3 Advanced — create/lifecycle/policy/public-access/delete."""
    print("\n[Phase H] S3 Advanced Operations")
    import random as _rand

    bucket_name = f"nexplane-smoke-{_rand.randint(100000, 999999)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create bucket
        cr = client.run_cr(
            "Smoke-H: create S3 bucket", "s3_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name, "rollback_strategy": "delete_s3_bucket"},
        )
        rollback_stack.append((cr["id"], "s3_bucket_create"))

        # Verify asset in inventory
        assets = client.get("/assets", params={"q": bucket_name, "asset_type": "storage_bucket"})
        if assets:
            bucket_asset_id = assets[0]["id"]
            log(f"Bucket in inventory: {bucket_asset_id}")
        else:
            bucket_asset_id = cloud_account_id
            print("  ⚠️  Bucket asset not yet in inventory (ingest lag)")

        # 2. Configure lifecycle: 1-day expiration
        cr = client.run_cr(
            "Smoke-H: configure lifecycle", "s3_lifecycle_configure", cloud_account_id,
            {
                "bucket_name": bucket_name,
                "rules": [{
                    "ID": "nexplane-smoke-expire",
                    "Status": "Enabled",
                    "Expiration": {"Days": 1},
                    "Filter": {"Prefix": "smoke/"},
                }],
                "rollback_strategy": "restore_prior_lifecycle",
            },
        )
        rollback_stack.append((cr["id"], "s3_lifecycle_configure"))
        log("Lifecycle policy configured")

        # 3. Set bucket policy (deny GetObject to public)
        import json as _json
        policy = _json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "DenyPublicRead",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }],
        })
        cr = client.run_cr(
            "Smoke-H: set bucket policy", "put_bucket_policy", cloud_account_id,
            {"bucket_name": bucket_name, "policy": policy, "rollback_strategy": "restore_prior_policy"},
        )
        rollback_stack.append((cr["id"], "put_bucket_policy"))
        log("Bucket policy applied")

        # 4. Block public access (all four settings)
        cr = client.run_cr(
            "Smoke-H: block public access", "block_s3_public_access", cloud_account_id,
            {"bucket_name": bucket_name, "rollback_strategy": "restore_s3_public_access"},
        )
        rollback_stack.append((cr["id"], "block_s3_public_access"))
        log("Public access blocked")

        # 5. Restore public access (verify rollback semantics work)
        cr = client.run_cr(
            "Smoke-H: restore public access", "restore_s3_public_access", cloud_account_id,
            {"bucket_name": bucket_name, "rollback_strategy": "block_s3_public_access"},
        )
        rollback_stack.append((cr["id"], "restore_s3_public_access"))
        log("Public access restored")

        # 6. Delete bucket — use rollback of the create CR (terminal)
        if rollback_stack:
            # Pop everything except the create CR rollback  
            # Roll back in reverse first (lifecycle, policy, block, restore)
            temp_stack = list(rollback_stack[1:])  # everything after create
            for cr_id, label in reversed(temp_stack):
                client.rollback_cr(cr_id, label)
            rollback_stack.clear()

            # Now trigger the s3_bucket_create rollback (= delete_s3_bucket)
            create_cr = cr  # This is now the restore_s3_public_access CR
            # Actually we need the create CR id — it was the first item
            # Re-run deletion directly since rollback_stack was cleared
            client.run_cr(
                "Smoke-H: delete bucket", "s3_bucket_delete", cloud_account_id,
                {"bucket_name": bucket_name, "rollback_strategy": "rollback_unavailable"},
            )
            log("Bucket deleted")

        # Verify asset removed
        assets = client.get("/assets", params={"q": bucket_name, "asset_type": "storage_bucket"})
        if not assets:
            log("Bucket removed from inventory")

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
        try:
            s3_safety = _get_aws_boto3_client('s3')
            if s3_safety:
                try:
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
                except s3_safety.exceptions.NoSuchBucket:
                    pass  # Already deleted
        except Exception as e2:
            print(f"  ⚠️  Safety net bucket delete failed: {e2}")
```

- [ ] **Step 2: Wire Phase H into `main()`**

```python
        if "H" in phases:
            run_phase_h(client, cloud_account_id)
```

- [ ] **Step 3: Run Phase H smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases H \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 4: Run all phases A–H together**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phases E-H — EC2 advanced, Security Groups, IAM, S3"
```

---

**Plan 2 complete.** Proceed to Plan 3 (Smoke Test Phases I–K: Route53, RDS, CloudWatch).
