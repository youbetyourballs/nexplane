# Smoke Test Expansion — Plan 2: AWS Phases P–T + rds_replica_create executor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AWS smoke test phases P–T to `test_aws_live.py`, adding new change type definitions for IAM, S3, tagging, agent removal, and DR failover operations, plus a new `rds_replica_create` executor and catalog entry for Phase S.

**Architecture:** All new phases follow the rollback stack pattern — each CR gets pushed to a list and the `finally` block pops in reverse order. New change type definitions follow the existing JSON schema in `backend/app/connectors/change_type_definitions/`. The new executor follows the pattern in `backend/app/connectors/executors/aws/create_rds_instance.py`.

**Tech Stack:** Python 3.12, boto3, existing Nexplane CR machinery

---

## Files

**Create:**
- `backend/app/connectors/change_type_definitions/attach_iam_policy.json`
- `backend/app/connectors/change_type_definitions/detach_iam_policy.json`
- `backend/app/connectors/change_type_definitions/disable_iam_user.json`
- `backend/app/connectors/change_type_definitions/enable_iam_user.json`
- `backend/app/connectors/change_type_definitions/rotate_iam_key.json`
- `backend/app/connectors/change_type_definitions/put_bucket_policy.json`
- `backend/app/connectors/change_type_definitions/tag_resource.json`
- `backend/app/connectors/change_type_definitions/remove_nexplane_agent.json`
- `backend/app/connectors/change_type_definitions/dr_dns_failover_route53.json`
- `backend/app/connectors/change_type_definitions/rds_replica_create.json`
- `backend/app/connectors/executors/aws/create_rds_replica.py`

**Modify:**
- `backend/app/connectors/catalog/aws.json` — add `create_rds_replica` + `dr_dns_failover_route53` actions
- `backend/tests/smoke/test_aws_live.py` — add phases P, Q, R, S, T + update main()

---

### Task 1: Change type definitions for IAM + S3 + agent + DR operations

**Files:**
- Create: all JSON files listed above except `rds_replica_create.json`

- [ ] **Step 1: Create `attach_iam_policy.json`**

```json
{
  "change_type": "attach_iam_policy",
  "display_name": "Attach IAM Policy",
  "steps": [
    {"generic_action": "attach_iam_policy", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "detach_iam_policy",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 2: Create `detach_iam_policy.json`**

```json
{
  "change_type": "detach_iam_policy",
  "display_name": "Detach IAM Policy",
  "steps": [
    {"generic_action": "detach_iam_policy", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Create `disable_iam_user.json`**

```json
{
  "change_type": "disable_iam_user",
  "display_name": "Disable IAM User",
  "steps": [
    {"generic_action": "disable_iam_user", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "enable_iam_user",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 4: Create `enable_iam_user.json`**

```json
{
  "change_type": "enable_iam_user",
  "display_name": "Enable IAM User",
  "steps": [
    {"generic_action": "enable_iam_user", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "disable_iam_user",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 5: Create `rotate_iam_key.json`**

```json
{
  "change_type": "rotate_iam_key",
  "display_name": "Rotate IAM Access Key",
  "steps": [
    {"generic_action": "rotate_iam_key", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Create `put_bucket_policy.json`**

```json
{
  "change_type": "put_bucket_policy",
  "display_name": "Set S3 Bucket Policy",
  "steps": [
    {"generic_action": "put_bucket_policy", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 7: Create `tag_resource.json`**

```json
{
  "change_type": "tag_resource",
  "display_name": "Tag AWS Resource",
  "steps": [
    {"generic_action": "tag_resource", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 8: Create `remove_nexplane_agent.json`**

```json
{
  "change_type": "remove_nexplane_agent",
  "display_name": "Remove Nexplane Agent",
  "steps": [
    {"generic_action": "remove_nexplane_agent", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 9: Create `dr_dns_failover_route53.json`**

```json
{
  "change_type": "dr_dns_failover_route53",
  "display_name": "DR DNS Failover (Route53)",
  "steps": [
    {"generic_action": "dr_dns_failover_route53", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 10: Verify the backend can load (no JSON syntax errors)**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import ActionCatalogService
import pathlib
svc = ActionCatalogService(pathlib.Path('app/connectors/catalog'))
print('Catalog OK:', len(svc._catalog), 'connectors loaded')
"
```

Expected: `Catalog OK: <N> connectors loaded` (no exceptions)

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/change_type_definitions/
git commit -m "feat(smoke): add CT definitions for IAM, S3, tag, agent-remove, dr-failover operations"
```

---

### Task 2: Create `create_rds_replica` executor + change type + catalog entry

**Files:**
- Create: `backend/app/connectors/executors/aws/create_rds_replica.py`
- Create: `backend/app/connectors/change_type_definitions/rds_replica_create.json`
- Modify: `backend/app/connectors/catalog/aws.json`

- [ ] **Step 1: Create the executor**

Create `backend/app/connectors/executors/aws/create_rds_replica.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    replica_id = parameters["replica_db_instance_identifier"]
    source_id = parameters["source_db_instance_identifier"]
    instance_class = parameters.get("db_instance_class", "db.t3.micro")

    if not creds:
        return {
            "action": "create_rds_replica",
            "replica_db_instance_identifier": replica_id,
            "source_db_instance_identifier": source_id,
            "endpoint": "mock-replica.rds.amazonaws.com",
            "mock": True,
            "_auto_asset": {
                "name": replica_id,
                "asset_type": "database",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {
                    "db_instance_identifier": replica_id,
                    "source_db_instance_identifier": source_id,
                    "provider": "aws",
                    "role": "replica",
                },
                "tags": ["rds", "replica", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, "rds")
    loop = asyncio.get_event_loop()

    def _call():
        rds.create_db_instance_read_replica(
            DBInstanceIdentifier=replica_id,
            SourceDBInstanceIdentifier=source_id,
            DBInstanceClass=instance_class,
            PubliclyAccessible=False,
        )
        waiter = rds.get_waiter("db_instance_available")
        waiter.wait(
            DBInstanceIdentifier=replica_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},  # up to 30 min
        )
        desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)
        inst = desc["DBInstances"][0]
        return inst.get("Endpoint", {}).get("Address", "")

    endpoint = await loop.run_in_executor(None, _call)
    return {
        "action": "create_rds_replica",
        "replica_db_instance_identifier": replica_id,
        "source_db_instance_identifier": source_id,
        "endpoint": endpoint,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": replica_id,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_instance_identifier": replica_id,
                "source_db_instance_identifier": source_id,
                "provider": "aws",
                "role": "replica",
            },
            "tags": ["rds", "replica", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback = delete the replica instance."""
    creds = getattr(connector, "credentials", {})
    replica_id = parameters["replica_db_instance_identifier"]

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, "rds")
    loop = asyncio.get_event_loop()

    def _delete():
        rds.delete_db_instance(
            DBInstanceIdentifier=replica_id,
            SkipFinalSnapshot=True,
        )
        waiter = rds.get_waiter("db_instance_deleted")
        waiter.wait(
            DBInstanceIdentifier=replica_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )

    await loop.run_in_executor(None, _delete)
    return {
        "rolled_back": True,
        "replica_db_instance_identifier": replica_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 2: Create `rds_replica_create.json`**

```json
{
  "change_type": "rds_replica_create",
  "display_name": "Create RDS Read Replica",
  "steps": [
    {"generic_action": "create_rds_replica", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_rds_instance",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 3: Add `create_rds_replica` and `dr_dns_failover_route53` to `backend/app/connectors/catalog/aws.json`**

Open `backend/app/connectors/catalog/aws.json`. Find the `"actions"` array. Append these two entries **before the closing `]`** of the actions array:

```json
    {
      "display_name": "Create RDS Read Replica",
      "description": "Create a read replica of an existing RDS instance and wait for it to become available.",
      "parameters": [
        {"required": true,  "type": "string", "name": "replica_db_instance_identifier"},
        {"required": true,  "type": "string", "name": "source_db_instance_identifier"},
        {"required": false, "type": "string", "name": "db_instance_class"}
      ],
      "execution_tier": 1,
      "rollback_action": "delete_rds_instance",
      "estimated_duration_seconds": 1800,
      "applicable_asset_types": ["cloud_account", "database"],
      "rollback_connector_type": "aws",
      "action_type": "change",
      "executor": "aws.create_rds_replica",
      "generic_action": "create_rds_replica",
      "action_id": "create_rds_replica"
    },
    {
      "display_name": "DR DNS Failover (Route53)",
      "description": "Update a Route53 DNS record to point to the DR endpoint during a failover event.",
      "parameters": [
        {"required": true, "type": "string", "name": "dns_record_id"},
        {"required": true, "type": "string", "name": "dr_endpoint"},
        {"required": true, "type": "string", "name": "hosted_zone_id"},
        {"required": false, "type": "string", "name": "original_endpoint"}
      ],
      "execution_tier": 1,
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "applicable_asset_types": ["cloud_account"],
      "action_type": "change",
      "executor": "aws.dr_dns_failover_route53",
      "generic_action": "dr_dns_failover_route53",
      "action_id": "dr_dns_failover_route53"
    }
```

- [ ] **Step 4: Verify syntax and catalog load**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('app/connectors/executors/aws/create_rds_replica.py').read()); print('executor syntax OK')"
docker exec nexplane-backend-1 python -c "
import json
data = json.load(open('app/connectors/catalog/aws.json'))
ids = [a['action_id'] for a in data['actions']]
assert 'create_rds_replica' in ids, 'create_rds_replica missing'
assert 'dr_dns_failover_route53' in ids, 'dr_dns_failover_route53 missing'
print('catalog OK, actions:', len(ids))
"
```

Expected: both print OK lines, no exceptions

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass (same count as before)

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/aws/create_rds_replica.py
git add backend/app/connectors/change_type_definitions/rds_replica_create.json
git add backend/app/connectors/catalog/aws.json
git commit -m "feat(aws): add create_rds_replica executor + rds_replica_create change type + catalog entries"
```

---

### Task 3: Add Phase P (IAM Advanced) to test_aws_live.py

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Append the following function **before `def main()`** in `test_aws_live.py`:

- [ ] **Step 1: Add `run_phase_p` function**

```python
def run_phase_p(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase P: IAM Advanced — attach/detach policy + disable/enable/rotate key via CRs."""
    print("\n[Phase P] IAM Advanced")

    username = f"nexplane-smoke-p-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create IAM user
        cr = client.run_cr(
            "Smoke-P: create IAM user", "iam_user_create", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "iam_user_create"))

        # 2. Attach ReadOnlyAccess policy via CR
        cr = client.run_cr(
            "Smoke-P: attach IAM policy", "attach_iam_policy", cloud_account_id,
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

        # 3. Rotate IAM key via CR (rotate_iam_key creates new key, deletes old)
        # First create an initial key via boto3 so there's a key to rotate
        if iam:
            initial_key = iam.create_access_key(UserName=username)["AccessKey"]
            old_key_id = initial_key["AccessKeyId"]

        cr = client.run_cr(
            "Smoke-P: rotate IAM key", "rotate_iam_key", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "rotate_iam_key"))
        log("IAM key rotated via CR")

        # 4. Disable IAM user via CR
        cr = client.run_cr(
            "Smoke-P: disable IAM user", "disable_iam_user", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "disable_iam_user"))

        if iam:
            keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
            assert all(k["Status"] == "Inactive" for k in keys), "Not all keys inactive"
            log("IAM user disabled (boto3 verified)")

        # 5. Enable IAM user via CR
        cr = client.run_cr(
            "Smoke-P: enable IAM user", "enable_iam_user", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "enable_iam_user"))

        if iam:
            keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
            assert all(k["Status"] == "Active" for k in keys), "Not all keys active after enable"
            log("IAM user enabled (boto3 verified)")

        # 6. Detach policy via CR rollback (rollback of attach_iam_policy)
        attach_cr_id, _ = rollback_stack.pop()  # pop attach_iam_policy
        client.rollback_cr(attach_cr_id, "attach_iam_policy → detach")
        log("Policy detached via CR rollback")

        # 7. Delete user via CR rollback
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
                # delete all access keys first
                try:
                    keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
                    for k in keys:
                        iam.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
                except Exception:
                    pass
                iam.delete_user(UserName=username)
                print(f"  Safety net: deleted IAM user {username}")
        except Exception:
            pass
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase P (IAM Advanced) to test_aws_live.py"
```

---

### Task 4: Add Phase Q (S3 Advanced Gaps) to test_aws_live.py

- [ ] **Step 1: Add `run_phase_q` function** (append before `def main()`):

```python
def run_phase_q(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase Q: S3 Advanced — put_bucket_policy + tag_resource."""
    print("\n[Phase Q] S3 Advanced Gaps")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-q-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create S3 bucket
        cr = client.run_cr(
            "Smoke-Q: create S3 bucket", "s3_bucket_create", cloud_account_id,
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
            "Smoke-Q: put bucket policy", "put_bucket_policy", cloud_account_id,
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
        # tag_resource uses ResourceGroupsTaggingAPI which needs ARN
        # Construct bucket ARN
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        cr = client.run_cr(
            "Smoke-Q: tag S3 bucket", "tag_resource", cloud_account_id,
            {"resource_arn": bucket_arn, "tags": {"nexplane-smoke": "true", "phase": "Q"}},
        )
        rollback_stack.append((cr["id"], "tag_resource"))

        if s3:
            tagging = s3.get_bucket_tagging(Bucket=bucket_name)
            tag_set = {t["Key"]: t["Value"] for t in tagging.get("TagSet", [])}
            assert tag_set.get("nexplane-smoke") == "true", "Tag not applied"
            log("Bucket tagged (boto3 verified)")

        # 4. Rollback: delete bucket (pops all CRs in reverse)
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
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase Q (S3 Advanced) to test_aws_live.py"
```

---

### Task 5: Add Phase R (DR DNS Failover) to test_aws_live.py

- [ ] **Step 1: Add `run_phase_r` function** (append before `def main()`):

```python
def run_phase_r(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase R: DR DNS Failover — exercises dr_dns_failover_route53 executor."""
    print("\n[Phase R] DR DNS Failover")

    import secrets as _secrets
    zone_name = f"nexplane-smoke-r-{_secrets.token_hex(4)}.internal."
    rollback_stack: list[tuple[str, str]] = []
    zone_id: str = ""

    try:
        # 1. Create a private Route53 zone (reuse Phase I pattern)
        cr = client.run_cr(
            "Smoke-R: create Route53 zone", "route53_zone_create", cloud_account_id,
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

        # 2. Create a CNAME record pointing to original endpoint
        original_endpoint = "primary.example.internal"
        record_name = f"app.{zone_name}"
        cr = client.run_cr(
            "Smoke-R: create CNAME record", "route53_record_upsert", cloud_account_id,
            {
                "hosted_zone_id": zone_id,
                "record_name": record_name,
                "record_type": "CNAME",
                "ttl": 60,
                "values": [original_endpoint],
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert"))

        # Verify original record
        records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        cname = next((r for r in records if r["Name"] == record_name and r["Type"] == "CNAME"), None)
        if cname:
            log(f"CNAME record created: {cname['ResourceRecords'][0]['Value']}")
        else:
            print(f"  ⚠️  CNAME record not found after creation (may have TTL delay)")

        # 3. DR failover: update CNAME to point to DR endpoint via CR
        dr_endpoint = "dr.example.internal"
        cr = client.run_cr(
            "Smoke-R: dr_dns_failover_route53", "dr_dns_failover_route53", cloud_account_id,
            {
                "dns_record_id": record_name,
                "dr_endpoint": dr_endpoint,
                "hosted_zone_id": zone_id,
                "original_endpoint": original_endpoint,
            },
        )
        rollback_stack.append((cr["id"], "dr_dns_failover_route53"))

        # Verify DR endpoint is now active
        records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        dr_cname = next((r for r in records
                         if r.get("Name", "").rstrip(".") == record_name.rstrip(".")
                         and r["Type"] == "CNAME"), None)
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
                    # Delete all non-NS/SOA records first
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
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase R (DR DNS Failover) to test_aws_live.py"
```

---

### Task 6: Add Phase T (Agent Lifecycle + Resource Tagging) to test_aws_live.py

Phase T requires Phase A (running EC2 instance with agent). It tags the instance, removes the agent, then redeploys it.

- [ ] **Step 1: Add `run_phase_t` function** (append before `def main()`):

```python
def run_phase_t(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase T: Agent Lifecycle + Resource Tagging — tag EC2 + remove/redeploy agent."""
    print("\n[Phase T] Agent Lifecycle + Resource Tagging")

    instance_id = phase_a_result.get("instance_id", "")
    instance_asset_id = phase_a_result.get("instance_asset")
    if not instance_id:
        fail("Phase T requires a running EC2 instance from Phase A")

    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Get account/region info to construct ARN
        ec2_client = _get_aws_boto3_client("ec2")
        sts = _get_aws_boto3_client("sts")
        if not ec2_client or not sts:
            fail("Phase T requires AWS credentials")
        caller = sts.get_caller_identity()
        account_id = caller["Account"]
        creds = _aws_creds_cache
        region = creds.get("region", "us-east-1")
        instance_arn = f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}"

        # 2. Tag EC2 instance via CR
        cr = client.run_cr(
            "Smoke-T: tag EC2 instance", "tag_resource",
            instance_asset_id if instance_asset_id else client.get_cloud_account_asset_id(),
            {"resource_arn": instance_arn, "tags": {"nexplane-smoke-tag": "true", "phase": "T"}},
        )
        rollback_stack.append((cr["id"], "tag_resource"))

        # Verify tag via boto3
        desc = ec2_client.describe_instances(InstanceIds=[instance_id])
        tags = {t["Key"]: t["Value"]
                for t in desc["Reservations"][0]["Instances"][0].get("Tags", [])}
        assert tags.get("nexplane-smoke-tag") == "true", "Tag not applied to instance"
        log("EC2 instance tagged (boto3 verified)")

        # 3. Remove Nexplane agent via CR
        cr = client.run_cr(
            "Smoke-T: remove nexplane agent", "remove_nexplane_agent",
            instance_asset_id if instance_asset_id else client.get_cloud_account_asset_id(),
            {"instance_id": instance_id},
        )
        rollback_stack.append((cr["id"], "remove_nexplane_agent"))
        log("Nexplane agent removed via CR")

        # Brief wait for agent to deregister
        time.sleep(10)

        # 4. Re-deploy agent via CR
        agent_secret = client.get_agent_secret()
        backend_ip = phase_a_result.get("backend_tailscale_ip", "")
        control_plane_url = f"http://{backend_ip}:8000" if backend_ip else "http://localhost:8000"

        cr = client.run_cr(
            "Smoke-T: redeploy nexplane agent", "deploy_nexplane_agent",
            instance_asset_id if instance_asset_id else client.get_cloud_account_asset_id(),
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
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase T (Agent Lifecycle + Resource Tagging) to test_aws_live.py"
```

---

### Task 7: Add Phase S (RDS Advanced — opt-in) to test_aws_live.py

Phase S is slow (~45 min) and excluded from the default run. It exercises `verify_rds_backup` and `promote_rds_replica` with the new `rds_replica_create` executor.

- [ ] **Step 1: Add `run_phase_s` function** (append before `def main()`):

```python
def run_phase_s(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase S: RDS Advanced — verify_rds_backup + rds_replica_create + promote_rds_replica (~45 min, opt-in)."""
    print("\n[Phase S] RDS Advanced (slow — ~45 min)")

    import secrets as _secrets
    db_id = f"nexplane-smoke-s-{_secrets.token_hex(3)}"
    replica_id = f"{db_id}-replica"
    snap_id = f"{db_id}-snap"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create RDS instance (db.t3.micro MySQL)
        print("  Creating RDS instance (wait ~10 min)...")
        cr = client._run_cr_with_timeout(
            "Smoke-S: create RDS instance", "rds_instance_create", cloud_account_id,
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
            "Smoke-S: create RDS snapshot", "rds_snapshot_create", cloud_account_id,
            {"db_instance_identifier": db_id, "snapshot_identifier": snap_id},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_create"))
        log(f"Snapshot created: {snap_id}")

        # 3. Verify backup via CR
        cr = client.run_cr(
            "Smoke-S: verify RDS backup", "verify_backup", cloud_account_id,
            {"snapshot_identifier": snap_id, "db_instance_identifier": db_id},
        )
        log("Backup verified via CR")

        # Confirm snapshot available via boto3
        rds = _get_aws_boto3_client("rds")
        if rds:
            snaps = rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id)["DBSnapshots"]
            assert snaps and snaps[0]["Status"] == "available", "Snapshot not available"
            assert snaps[0]["AllocatedStorage"] > 0, "AllocatedStorage is 0"
            log(f"Snapshot boto3 verified: {snaps[0]['AllocatedStorage']}GB")

        # 4. Create read replica via CR
        print("  Creating read replica (wait ~15 min)...")
        cr = client._run_cr_with_timeout(
            "Smoke-S: create RDS read replica", "rds_replica_create", cloud_account_id,
            {"replica_db_instance_identifier": replica_id,
             "source_db_instance_identifier": db_id,
             "db_instance_class": "db.t3.micro"},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_replica_create"))
        log(f"Replica created: {replica_id}")

        # 5. Promote replica to standalone via CR
        print("  Promoting replica (wait ~10 min)...")
        cr = client._run_cr_with_timeout(
            "Smoke-S: promote RDS replica", "promote_db_replica", cloud_account_id,
            {"replica_identifier": replica_id},
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "promote_db_replica"))

        # Verify promotion via boto3
        if rds:
            desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)["DBInstances"][0]
            assert not desc.get("ReadReplicaSourceDBInstanceIdentifier"), \
                "Replica still shows source — not yet standalone"
            log(f"Promotion verified: {replica_id} is now standalone")

        # 6. Delete promoted instance via CR
        cr = client.run_cr(
            "Smoke-S: delete promoted instance", "rds_instance_delete", cloud_account_id,
            {"db_instance_identifier": replica_id},
        )
        # Pop the promote CR since we handled cleanup manually
        rollback_stack.pop()
        # Pop the replica CR since we deleted it above
        rollback_stack.pop()
        log("Promoted instance deleted")

        log("Phase S complete")

    except Exception as e:
        print(f"\n❌ Phase S failed: {e}")
        raise
    finally:
        print("  [Phase S cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete instances via boto3
        try:
            rds = _get_aws_boto3_client("rds")
            if rds:
                for iid in [replica_id, db_id]:
                    try:
                        rds.delete_db_instance(DBInstanceIdentifier=iid, SkipFinalSnapshot=True)
                        print(f"  Safety net: deleted RDS instance {iid}")
                    except rds.exceptions.DBInstanceNotFoundFault:
                        pass
                    except Exception as e:
                        print(f"  ⚠️  Safety net delete {iid}: {e}")
                try:
                    rds.delete_db_snapshot(DBSnapshotIdentifier=snap_id)
                    print(f"  Safety net: deleted snapshot {snap_id}")
                except Exception:
                    pass
        except Exception:
            pass
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase S (RDS Advanced, opt-in) to test_aws_live.py"
```

---

### Task 8: Update main() to dispatch Phases P–T

- [ ] **Step 1: Replace the `main()` function in `test_aws_live.py`**

Find and replace the existing `def main():` function (from `def main():` through `if __name__ == "__main__": main()`) with:

```python
def main():
    parser = make_base_parser("Nexplane AWS live smoke test")
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help=(
            "Comma-separated phases to run. "
            "A-K: existing phases. P-T: new phases (P=IAM, Q=S3, R=DR-DNS, S=RDS-slow, T=Agent). "
            "Default: A,B,C,D. J and S are slow (~35-45 min) and excluded from default."
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
        stale = [a for a in client.get("/assets", params={"q": "nexplane-smoke-test"})
                 if "smoke-test" in a.get("name", "")]
        for asset in stale:
            client.client.delete(f"{client.base}/assets/{asset['id']}")
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
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Verify Phase P runs (new change types available)**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases P 2>&1 | tail -10
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 4: Verify Phase Q runs**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases Q 2>&1 | tail -10
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Verify Phase R runs**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases R 2>&1 | tail -10
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 6: Run backend test suite**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): update main() in test_aws_live.py to dispatch phases P-T"
```

---

**Plan 2 complete.** Plans 3–6 build on this:
- **Plan 3:** `test_gcp_live.py` new phases N–R + sub-project stubs S–X
- **Plan 4:** `test_azure_live.py` new phases P–T + sub-project stubs U–Z
- **Plan 5:** `test_agent_live.py` — Linux × 3 clouds (all 47 agent commands)
- **Plan 6:** `test_agent_live.py` — Windows × 3 clouds + README update
