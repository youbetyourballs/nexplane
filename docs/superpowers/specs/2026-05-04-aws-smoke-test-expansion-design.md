# AWS Smoke Test Expansion — Phases E–K Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand the Nexplane live AWS smoke test from 4 phases (A–D) to 11 phases (A–K), adding full lifecycle coverage for EC2 advanced operations, Security Groups, IAM, S3, Route53, RDS, and CloudWatch — with every cleanup driven by the Nexplane rollback system.

**Architecture:** Infrastructure lifecycle narrative — spin up, protect, identify, store, resolve, back up, observe, tear down. Each phase uses new executors and change types that also get wired into the CR workflow UI and asset Quick Actions panel.

**Tech Stack:** Python (smoke test), FastAPI/boto3 (executors), React/TypeScript (UI), PostgreSQL (asset inventory)

---

## Smoke Test Conventions (applies to all future connector expansions)

### Rollback Stack Pattern

Every smoke test phase MUST use a LIFO rollback stack as its primary cleanup mechanism. Direct SDK calls (boto3, Azure SDK, etc.) are the safety net only — not the primary path.

```python
rollback_stack: list[str] = []  # CR IDs, pushed on successful execute

# In finally block:
for cr_id in reversed(rollback_stack):
    try:
        client.post(f"/change-requests/{cr_id}/rollback")
        client._wait_rollback(cr_id, label=f"rollback {cr_id}")
    except Exception as e:
        print(f"  ⚠️  Rollback failed for {cr_id}: {e}")
        # Fall through to boto3 safety net below
```

**Why:** This exercises both the forward execution path and the rollback path on every test run. It validates that rollbacks work before they're needed in production.

Three operation categories fall through to direct SDK cleanup (not rollback):
1. **Terminal deletes** — `iam_user_delete`, `s3_bucket_delete`, `rds_instance_delete` (no inverse exists)
2. **Test scaffolding** — resources created outside the CR system (e.g., boto3-direct security group in Phase F)
3. **Rollback failures** — when a rollback CR itself fails, direct SDK ensures cost management

This pattern applies to all future connector smoke tests: Azure, GCP, Tailscale, and any other connector added to the platform.

### Cost Management

- All created resources must be registered in a `cleanup_registry` dict at creation time
- The `finally` block always runs, even on exception
- No resource should survive a test run (pass or fail)
- Phase-level timeouts enforce maximum wall-clock time per phase

### Teardown Ordering

Resources are torn down in reverse creation order (LIFO). Dependencies dictate some fixed orderings — e.g., RDS snapshots before instances, Route53 records before hosted zones.

---

## New Executors (15 files)

All executors follow the existing pattern: real boto3 when credentials exist, mock return when not. All live in `backend/app/connectors/executors/aws/`.

### IAM

**`create_iam_user.py`**
- `iam.create_user(UserName=username, Tags=[...])`
- `iam.create_access_key(UserName=username)`
- Returns: `{action, username, access_key_id, _auto_asset: {asset_type: "identity", ...}}`
- Rollback: `delete_iam_user`

**`delete_iam_user.py`**
- List and delete all access keys, detach all managed policies, delete login profile, delete user
- Returns: `{action, username, deleted: true}`
- Rollback: none (terminal)

### S3

**`create_s3_bucket.py`**
- `s3.create_bucket(Bucket=bucket_name)` with region handling
- Apply `ManagedBy: nexplane` tag
- Returns: `{action, bucket_name, region, _auto_asset: {asset_type: "storage_bucket", ...}}`
- Rollback: `delete_s3_bucket`

**`delete_s3_bucket.py`**
- List all object versions and delete markers, batch-delete, then `s3.delete_bucket`
- Returns: `{action, bucket_name, deleted: true}`
- Rollback: none (terminal)

**`set_s3_lifecycle.py`**
- `s3.put_bucket_lifecycle_configuration(Bucket=bucket_name, LifecycleConfiguration={Rules:[...]})`
- Stores prior rules in `carried_context` for rollback
- Returns: `{action, bucket_name, rules_applied: N}`
- Rollback: `set_s3_lifecycle` with prior rules from `carried_context` (or delete configuration if none)

### Route53

**`create_route53_zone.py`**
- `route53.create_hosted_zone(Name=zone_name, CallerReference=unique_ref, HostedZoneConfig={PrivateZone=True, ...})`
- Returns: `{action, zone_id, zone_name, _auto_asset: {asset_type: "dns_zone", ...}}`
- Rollback: `delete_route53_zone`

**`delete_route53_zone.py`**
- List all non-SOA/NS records, batch-delete them, then `route53.delete_hosted_zone`
- Returns: `{action, zone_id, deleted: true}`
- Rollback: none (terminal)

**`upsert_route53_record.py`**
- `route53.change_resource_record_sets` with `Action: UPSERT`
- Supports: A, CNAME, weighted routing (Weight + SetIdentifier params)
- Stores prior record value in `carried_context` for rollback
- Returns: `{action, zone_id, name, type, value, change_id}`
- Rollback: `delete_route53_record` or `upsert_route53_record` with prior value

**`delete_route53_record.py`**
- `route53.change_resource_record_sets` with `Action: DELETE`
- Returns: `{action, zone_id, name, type, deleted: true}`
- Rollback: `upsert_route53_record` with value from `carried_context`

### RDS

**`create_rds_instance.py`**
- `rds.create_db_instance(DBInstanceIdentifier, DBInstanceClass="db.t3.micro", Engine, MasterUsername, MasterUserPassword, ...)`
- Waiter: `db_instance_available` (up to 20 minutes)
- Returns: `{action, db_instance_id, endpoint, port, engine, _auto_asset: {asset_type: "database", ...}}`
- Rollback: `delete_rds_instance`

**`delete_rds_instance.py`**
- `rds.delete_db_instance(DBInstanceIdentifier, SkipFinalSnapshot=True)`
- Waiter: `db_instance_deleted` (up to 20 minutes)
- Returns: `{action, db_instance_id, deleted: true}`
- Rollback: none (terminal)

**`delete_rds_snapshot.py`**
- `rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)`
- Returns: `{action, snapshot_id, deleted: true}`
- Rollback: none (terminal — snapshot data is gone)

### CloudWatch

**`create_cloudwatch_alarm.py`**
- `cloudwatch.put_metric_alarm(AlarmName, MetricName, Namespace, Threshold, ComparisonOperator, EvaluationPeriods, ...)`
- Returns: `{action, alarm_name, metric_name, namespace, threshold}`
- Rollback: `delete_cloudwatch_alarm`

**`delete_cloudwatch_alarm.py`**
- `cloudwatch.delete_alarms(AlarmNames=[alarm_name])`
- Returns: `{action, alarm_names, deleted: true}`
- Rollback: `create_cloudwatch_alarm` with config from `carried_context`

### EBS (missing rollback executor)

**`delete_ebs_snapshot.py`**
- `ec2.delete_snapshot(SnapshotId=snapshot_id)`
- Returns: `{action, snapshot_id, deleted: true}`
- Rollback: none (terminal)
- *Required as rollback target for `create_ebs_snapshot` and `snapshot_asset`*

---

## New Change Type Definitions (12 files)

All in `backend/app/connectors/change_type_definitions/`.

### IAM

**`iam_user_create.json`**
```json
{
  "change_type": "iam_user_create",
  "display_name": "Create IAM User",
  "steps": [
    {"generic_action": "create_iam_user", "purpose": "execute", "required": true}
  ],
  "rollback_action": "delete_iam_user"
}
```

**`iam_user_delete.json`**
```json
{
  "change_type": "iam_user_delete",
  "display_name": "Delete IAM User",
  "steps": [
    {"generic_action": "detach_iam_policy", "purpose": "preflight_validate", "required": false},
    {"generic_action": "delete_iam_user", "purpose": "execute", "required": true}
  ],
  "rollback_action": null
}
```

### S3

**`s3_bucket_create.json`** — steps: `create_s3_bucket`; rollback: `delete_s3_bucket`

**`s3_bucket_delete.json`** — steps: `delete_s3_bucket`; rollback: none

**`s3_lifecycle_configure.json`** — steps: `set_s3_lifecycle`; rollback: `set_s3_lifecycle` (prior config from `carried_context`)

### Route53

**`route53_zone_create.json`** — steps: `create_route53_zone`; rollback: `delete_route53_zone`

**`route53_record_upsert.json`** — steps: `upsert_route53_record`; rollback: `delete_route53_record`

**`route53_record_delete.json`** — steps: `delete_route53_record`; rollback: `upsert_route53_record`

### RDS

**`rds_instance_create.json`** — steps: `create_rds_instance`; rollback: `delete_rds_instance`

**`rds_instance_delete.json`**
- Steps: `create_rds_snapshot` (safety backup) → `delete_rds_instance`
- Rollback: `restore_rds_snapshot` (restores from the safety backup taken in step 1)

**`rds_snapshot_create.json`** — steps: `create_rds_snapshot`; rollback: `delete_rds_snapshot`

### CloudWatch

**`cloudwatch_alarm_create.json`** — steps: `create_cloudwatch_alarm`; rollback: `delete_cloudwatch_alarm`

**`cloudwatch_alarm_delete.json`** — steps: `delete_cloudwatch_alarm`; rollback: `create_cloudwatch_alarm`

---

## Rollback Additions to Existing Change Types

These existing change types need `rollback_action` added or corrected:

| Change type | Missing rollback | Add |
|------------|-----------------|-----|
| `snapshot_asset` / `create_ebs_snapshot` | No rollback defined | `delete_ebs_snapshot` |
| `ec2_stop` | No rollback | `ec2_start` |
| `ec2_start` | No rollback | `ec2_stop` |
| `rds_snapshot_create` (if exists) | No rollback | `delete_rds_snapshot` |

---

## New Asset Type

**`dns_zone`** — added to `AssetType` enum (Alembic migration required).

Fields in `asset_metadata`:
- `zone_id` (Route53 hosted zone ID, e.g. `Z1234567890`)
- `zone_name` (FQDN with trailing dot, e.g. `smoke-test.nexplane.internal.`)
- `private_zone` (bool)
- `record_count` (int)
- `region` (for private zones)

New ingest action: `discover_route53_zones` in `aws.json` catalog, executor `discover_route53_zones.py` — uses `route53.list_hosted_zones_by_name`, upserts `dns_zone` assets via `_upsert_auto_asset`.

---

## Smoke Test Phases E–K

### Phase E — EC2 Advanced Operations
*Prerequisite: Phase A instance running*

Operations:
1. `ec2_stop` CR → push to rollback stack; verify `instance_stopped` in SSM
2. `ec2_start` CR → push; verify SSM reconnects; pop ec2_stop rollback (now superseded)
3. `ec2_reboot` CR → push; verify SSM responds post-reboot
4. `ssm_command` CR: `snapshot_asset` (create EBS snapshot) → push snapshot CR
5. `verify_snapshot` CR → assert `snapshot.state == completed`

Cleanup (rollback stack):
- Rollback snapshot CR → `delete_ebs_snapshot`
- Rollback ec2_reboot → no meaningful inverse (instance running, no-op)
- Rollback ec2_start → `ec2_stop` (leaves instance stopped for Phase A cleanup)

Safety net boto3: none needed (all via rollback)

Cost: ~$0.00 extra

---

### Phase F — Security Groups
*No EC2 instance dependency*

Setup (boto3 direct — test scaffolding):
- `ec2.create_security_group(GroupName="nexplane-smoke-sg-<ts>", Description="Smoke test")` → register in cleanup_registry

Operations:
1. `security_group_update` CR (add inbound TCP 8443 from `10.0.0.0/8`) → push to rollback stack
2. `validate_security_rules` CR → assert rule exists
3. `export_security_group` CR → capture rules into asset metadata
4. Second `security_group_update` CR (remove the rule) → push
5. `validate_security_rules` CR → assert rule gone

Cleanup (rollback stack):
- Rollback second update CR → restore the rule
- Rollback first update CR → remove the rule (net result: SG back to original)

Safety net boto3:
- `ec2.delete_security_group(GroupId=sg_id)` in finally

Cost: free

---

### Phase G — IAM User Lifecycle
*No EC2 dependency*

Operations:
1. `iam_user_create` CR (`nexplane-smoke-user-<ts>`) → push; assert `identity` asset in inventory
2. `attach_iam_policy` CR (attach `arn:aws:iam::aws:policy/ReadOnlyAccess`) → push
3. `rotate_iam_key` CR → **not pushed** to rollback stack (old key is deleted; no inverse exists; `rotate_iam_key` change type defines `rollback_action: null`)
4. `disable_iam_user` CR → push; assert user inactive
5. `enable_iam_user` CR → push; assert user active
6. `detach_iam_policy` CR → push
7. `iam_user_delete` CR (terminal — not pushed to rollback stack) → assert `identity` asset removed

Cleanup (rollback stack — reverse of steps 1-6):
- Rollback enable → `disable_iam_user`
- Rollback disable → `enable_iam_user`
- rotate_iam_key was not pushed (no rollback defined)
- Rollback attach_policy → `detach_iam_policy`
- Rollback iam_user_create → `delete_iam_user`

Safety net boto3: `iam.delete_user` chain in finally if CR-based delete didn't run

Cost: free

---

### Phase H — S3 Advanced
*No EC2 dependency*

Operations:
1. `s3_bucket_create` CR (`nexplane-smoke-bucket-<ts>`) → push; assert `storage_bucket` asset in inventory
2. `s3_lifecycle_configure` CR (1-day expiration rule) → push
3. `put_bucket_policy` CR (deny `s3:GetObject` to `Principal: "*"`) → push
4. `block_s3_public_access` CR (all four settings enabled) → push
5. `restore_s3_public_access` CR → push; verify settings cleared
6. `s3_bucket_delete` CR (terminal — not pushed) → assert `storage_bucket` asset removed

Cleanup (rollback stack — reverse of steps 1-5):
- Rollback block_public_access → `restore_s3_public_access`
- Rollback put_bucket_policy → remove bucket policy
- Rollback lifecycle → `set_s3_lifecycle` with empty/prior config
- Rollback s3_bucket_create → `delete_s3_bucket`

Safety net boto3: force-empty + delete bucket in finally if CR-based delete didn't run

Cost: free (empty bucket deleted same run)

---

### Phase I — Route53 DNS
*No EC2 dependency*

Setup: generate `smoke_ts = int(time.time())`; zone name = `smoke-{smoke_ts}.nexplane.internal`

Operations:
1. `route53_zone_create` CR (private hosted zone) → push; assert `dns_zone` asset in inventory
2. `route53_record_upsert` CR: A record `web.{zone_name}` → `10.0.0.1` → push
3. `route53_record_upsert` CR: update same record → `10.0.0.2` → push; assert UPSERT semantics
4. `route53_record_upsert` CR: weighted A `primary.{zone_name}` weight=100 → push
5. `route53_record_upsert` CR: weighted A `secondary.{zone_name}` weight=0 → push
6. `dr_dns_failover_route53` CR: swap weights (primary→0, secondary→100) → push
7. Assert weights via boto3 `list_resource_record_sets`
8. `route53_record_delete` CR: delete all test records → push

Cleanup (rollback stack):
- Rollback record deletes → `upsert_route53_record` (restore records)
- Rollback failover → swap weights back
- Rollback weighted records → `route53_record_delete`
- Rollback A record upserts → `route53_record_delete`
- Rollback zone_create → `delete_route53_zone`

Safety net boto3: `delete_route53_zone` with pre-delete record sweep in finally

Cost: ~$0.00 (private zone, seconds of existence)

Future extension: `--route53-zone-id Z123...` flag to run against an existing public hosted zone

---

### Phase J — RDS Full Lifecycle
*Phase timeout: 45 minutes*

Instance config: `db.t3.micro`, MySQL 8.0, `nexplane-smoke-db-<ts>`, `SkipFinalSnapshot=True` on delete

Operations:
1. `rds_instance_create` CR → push; waiter `db_instance_available`; assert `database` asset in inventory
2. `create_rds_snapshot` CR (`nexplane-smoke-snap-<ts>`) → push; waiter `db_snapshot_completed`
3. `verify_rds_backup` CR → assert snapshot restorable, size > 0
4. `restore_rds_snapshot` CR → new instance `nexplane-smoke-db-restored-<ts>` → push; waiter `db_instance_available`
5. `rds_instance_delete` CR on restored instance (terminal) → waiter `db_instance_deleted`; assert restored asset removed
6. `rds_instance_delete` CR on original instance (terminal) → waiter `db_instance_deleted`; assert original asset removed

Cleanup (rollback stack):
- Rollback create_rds_snapshot → `delete_rds_snapshot`
- Rollback rds_instance_create → `delete_rds_instance`

Safety net boto3 (in finally):
- Delete any registered snapshot IDs via `rds.delete_db_snapshot`
- Delete any registered instance IDs via `rds.delete_db_instance(SkipFinalSnapshot=True)`
- Poll until `db_instance_deleted` (up to 20 min timeout)

Cost: ~$0.05–$0.10 per run (two db.t3.micro × ~15 min each)

---

### Phase K — CloudWatch Alarms
*Prerequisite: Phase A instance running (for CPUUtilization metric)*

Operations:
1. `cloudwatch_alarm_create` CR: `CPUUtilization` alarm on Phase A instance, threshold 99% (won't fire on normal load) → push
2. `ssm_command` CR: `stress-ng --cpu 1 --timeout 5 --metrics` to generate metric data point
3. `cloudwatch_alarm_create` CR: custom namespace alarm (`Nexplane/SmokeTest`, metric `TestValue`, threshold 0, ComparisonOperator `GreaterThanThreshold`) → push
4. `ssm_command` CR: `aws cloudwatch put-metric-data --namespace Nexplane/SmokeTest --metric-name TestValue --value 1` → triggers alarm
5. Poll `describe_alarms` until `StateValue == ALARM` (up to 90s; CloudWatch evaluates every 60s)
6. `cloudwatch_alarm_delete` CR: delete both alarms (terminal — not pushed)

Cleanup (rollback stack):
- Rollback custom alarm create → `delete_cloudwatch_alarm`
- Rollback CPU alarm create → `delete_cloudwatch_alarm`

Safety net boto3: `cloudwatch.delete_alarms` on all registered alarm names in finally

Cost: ~$0.002 per run

---

## CR Workflow UI Changes

### `CreateChangeRequest.tsx` — New Groups and Types

New change type groups added to `CHANGE_TYPE_GROUPS`:

```
IAM
  iam_user_create     "Create IAM User"
  iam_user_delete     "Delete IAM User"

S3 Storage
  s3_bucket_create         "Create S3 Bucket"
  s3_bucket_delete         "Delete S3 Bucket"
  s3_lifecycle_configure   "Configure Lifecycle Policy"

DNS (Route53)
  route53_zone_create     "Create Hosted Zone"
  route53_record_upsert   "Create / Update DNS Record"
  route53_record_delete   "Delete DNS Record"

Database (RDS)
  rds_instance_create   "Create RDS Instance"
  rds_instance_delete   "Delete RDS Instance"
  rds_snapshot_create   "Create RDS Snapshot"
  rds_snapshot_restore  "Restore RDS Snapshot"

Observability
  cloudwatch_alarm_create   "Create CloudWatch Alarm"
  cloudwatch_alarm_delete   "Delete CloudWatch Alarm"
```

### `CHANGE_TYPE_ASSET_FILTER` additions

| Change type | Required asset type |
|------------|-------------------|
| `iam_user_create` | `cloud_account` |
| `iam_user_delete` | `identity` |
| `s3_bucket_create` | `cloud_account` |
| `s3_bucket_delete` | `storage_bucket` |
| `s3_lifecycle_configure` | `storage_bucket` |
| `route53_zone_create` | `cloud_account` |
| `route53_record_upsert` | `dns_zone` |
| `route53_record_delete` | `dns_zone` |
| `rds_instance_create` | `cloud_account` |
| `rds_instance_delete` | `database` |
| `rds_snapshot_create` | `database` |
| `rds_snapshot_restore` | `database` |
| `cloudwatch_alarm_create` | `server`, `database`, `cloud_account` |
| `cloudwatch_alarm_delete` | `cloud_account` |

---

## Asset Quick Actions Panel (`AssetDetail.tsx`)

`ASSET_ACTIONS` map additions by asset type. Each action navigates to the new CR flow with pre-filled `change_type` and `desired_outcome` seeded from `asset_metadata`.

### server (EC2)
*Existing:* Stop, Start, Join Tailscale, Deploy Agent, SSM Command
*Add:* Create EBS Snapshot → `snapshot_asset` (pre-fills `instance_id`), Reboot → `ec2_reboot`, Terminate → `ec2_terminate`, Update Security Group → `security_group_update`

### database (RDS)
Create Snapshot → `rds_snapshot_create` (pre-fills `db_instance_identifier`), Restore from Snapshot → `rds_snapshot_restore`, Delete Instance → `rds_instance_delete`, Reboot → `ssm_command` (RDS reboot via API)

### storage_bucket (S3)
Block Public Access → `block_s3_public_access` (pre-fills `bucket_name`), Set Bucket Policy → `put_bucket_policy`, Configure Lifecycle → `s3_lifecycle_configure`, Delete Bucket → `s3_bucket_delete`

### dns_zone (Route53 — new type)
Add Record → `route53_record_upsert` (pre-fills `zone_id`), Update Record → `route53_record_upsert`, Delete Record → `route53_record_delete`, DNS Failover → `dr_dns_failover_route53`

### identity (IAM user)
Disable User → `iam_user_disable` (pre-fills `username`), Enable User → `iam_user_enable`, Rotate Access Key → `rotate_iam_key`, Attach Policy → `attach_iam_policy`, Detach Policy → `detach_iam_policy`, Delete User → `iam_user_delete`

### firewall (Security Group)
Export Rules → `export_security_group` (pre-fills `security_group_id`), Update Rules → `security_group_update`, Validate Rules → `validate_security_rules`, Restore Rules → `restore_security_group`

### load_balancer (ELB)
Tag Resource → `tag_resource` *(ALB lifecycle actions deferred — see ALB sub-project in future tasks)*

### endpoint (agent-managed)
Update Agent → `deploy_nexplane_agent`, Run Patch Audit → `ssm_command` (patch audit command), Collect System Info → `ssm_command` (system info command)

### key_pair
Delete Key Pair → `key_pair_delete`

### cloud_account
Run Discovery → triggers ingest on the AWS connector

---

## Database Migration

One new Alembic migration required:
- Add `dns_zone` to `AssetType` enum
- No table schema changes (metadata stored in existing `metadata` JSONB column)

---

## Deferred

**ALB lifecycle** (create ALB, target group, register targets, listener management, delete) tracked as a dedicated future sub-project. Current smoke test exercises ELB discovery and tagging only. Quick Actions panel for `load_balancer` is limited to `tag_resource` until the ALB sub-project ships.

---

## Implementation Decomposition

Suggested plan breakdown (each becomes its own implementation plan):

1. **New executors + change type definitions** — 15 executors, 12 change type JSONs, `delete_ebs_snapshot` rollback fix, Alembic migration for `dns_zone`
2. **Smoke test Phases E–H** — EC2 advanced, Security Groups, IAM, S3 (with rollback stack)
3. **Smoke test Phases I–K** — Route53, RDS, CloudWatch (with rollback stack)
4. **UI — CR workflow + Quick Actions** — `CHANGE_TYPE_GROUPS`, `CHANGE_TYPE_ASSET_FILTER`, `ASSET_ACTIONS` for all asset types
