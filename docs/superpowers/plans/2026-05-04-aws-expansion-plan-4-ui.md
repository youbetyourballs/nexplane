# AWS Expansion — Plan 4: UI — CR Workflow + Asset Quick Actions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all 13 new change types into the Create Change Request modal (groups, asset filter, outcome templates) and expand Asset Quick Actions for every asset type to reflect the new AWS capabilities.

**Architecture:** Two files only. `frontend/src/types/api.ts` gains 13 new `ChangeType` literal values. `frontend/src/pages/CreateChangeRequest.tsx` gets new group entries and asset filter entries. `frontend/src/pages/AssetDetail.tsx` gets expanded `ASSET_ACTIONS` entries for every asset type.

**Tech Stack:** TypeScript, React 18, TanStack Query v5

**Prerequisite:** Plans 1–3 must be complete so the backend has the change types.

---

## Files

**Modify:**
- `frontend/src/types/api.ts` — add 13 ChangeType values
- `frontend/src/pages/CreateChangeRequest.tsx` — add to CHANGE_TYPE_GROUPS + CHANGE_TYPE_ASSET_FILTER + outcome templates
- `frontend/src/pages/AssetDetail.tsx` — expand ASSET_ACTIONS for all asset types

---

### Task 1: Add ChangeType values to frontend types

**Files:**
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Add new ChangeType literals**

Open `frontend/src/types/api.ts`. Find the `ChangeType` type (line ~243). Add the 13 new values:

```typescript
export type ChangeType =
  | "dns_update"
  | "snapshot_asset"
  | "security_group_update"
  | "key_rotation"
  | "telemetry_agent_deploy"
  | "remote_command"
  | "microsegmentation_policy"
  | "ec2_stop"
  | "ec2_start"
  | "ec2_reboot"
  | "ec2_stop_start"
  | "ec2_launch"
  | "ec2_terminate"
  | "rolling_restart"
  | "canary_config_push"
  | "distribute_file"
  | "fleet_health_check"
  | "key_pair_create"
  | "ssm_command"
  | "tailscale_join"
  | "tailscale_remove"
  | "deploy_nexplane_agent"
  | "terraform_local_apply"
  | "ansible_local_playbook"
  // AWS Expansion
  | "iam_user_create"
  | "iam_user_delete"
  | "s3_bucket_create"
  | "s3_bucket_delete"
  | "s3_lifecycle_configure"
  | "route53_zone_create"
  | "route53_record_upsert"
  | "route53_record_delete"
  | "rds_instance_create"
  | "rds_instance_delete"
  | "rds_snapshot_create"
  | "cloudwatch_alarm_create"
  | "cloudwatch_alarm_delete";
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | head -20"
```

Expected: no errors (or same errors as before this change — no new type errors)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/api.ts
git commit -m "feat(ui): add 13 new ChangeType values for AWS expansion"
```

---

### Task 2: CR workflow — CHANGE_TYPE_GROUPS

**Files:**
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Add new groups and entries to CHANGE_TYPE_GROUPS**

Open `frontend/src/pages/CreateChangeRequest.tsx`. Find `CHANGE_TYPE_GROUPS` (line ~381). Add the following groups after the existing "Database" group and before "Backup & Recovery":

```typescript
  {
    label: "IAM",
    types: ["iam_user_create", "iam_user_delete"],
  },
  {
    label: "S3 Storage",
    types: ["s3_bucket_create", "s3_bucket_delete", "s3_lifecycle_configure"],
  },
  {
    label: "DNS (Route53)",
    types: ["route53_zone_create", "route53_record_upsert", "route53_record_delete"],
  },
  {
    label: "RDS",
    types: ["rds_instance_create", "rds_instance_delete", "rds_snapshot_create"],
  },
  {
    label: "Observability",
    types: ["cloudwatch_alarm_create", "cloudwatch_alarm_delete"],
  },
```

Also add `"rds_snapshot_restore"` to the existing "Backup & Recovery" group:

```typescript
  {
    label: "Backup & Recovery",
    types: ["create_backup", "verify_backup", "restore_files", "dr_failover", "scheduled_reboot", "rds_snapshot_restore"],
  },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(ui): add IAM, S3, Route53, RDS, Observability groups to CR workflow"
```

---

### Task 3: CR workflow — CHANGE_TYPE_ASSET_FILTER

**Files:**
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Add asset filter entries**

In `CHANGE_TYPE_ASSET_FILTER`, add the following entries (around line ~434, after the existing entries):

```typescript
  // IAM
  iam_user_create: "cloud_account",
  iam_user_delete: "identity",
  // S3
  s3_bucket_create: "cloud_account",
  s3_bucket_delete: "storage_bucket",
  s3_lifecycle_configure: "storage_bucket",
  // Route53
  route53_zone_create: "cloud_account",
  route53_record_upsert: "dns_zone",
  route53_record_delete: "dns_zone",
  // RDS
  rds_instance_create: "cloud_account",
  rds_instance_delete: "database",
  rds_snapshot_create: "database",
  rds_snapshot_restore: "database",
  // CloudWatch
  cloudwatch_alarm_create: null,  // any asset type
  cloudwatch_alarm_delete: null,
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(ui): add CHANGE_TYPE_ASSET_FILTER entries for all new change types"
```

---

### Task 4: CR workflow — outcome templates

**Files:**
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

The `CHANGE_TYPE_LABELS` and `OUTCOME_TEMPLATES` maps (or equivalent) need entries for each new type. Find where existing outcome templates are defined (search for `"ec2_launch"` in the file to find the template structure).

- [ ] **Step 1: Add display labels**

Find the `CHANGE_TYPE_LABELS` map (or equivalent label map). Add:

```typescript
  iam_user_create: "Create IAM User",
  iam_user_delete: "Delete IAM User",
  s3_bucket_create: "Create S3 Bucket",
  s3_bucket_delete: "Delete S3 Bucket",
  s3_lifecycle_configure: "Configure S3 Lifecycle",
  route53_zone_create: "Create Hosted Zone",
  route53_record_upsert: "Create / Update DNS Record",
  route53_record_delete: "Delete DNS Record",
  rds_instance_create: "Create RDS Instance",
  rds_instance_delete: "Delete RDS Instance",
  rds_snapshot_create: "Create RDS Snapshot",
  rds_snapshot_restore: "Restore RDS Snapshot",
  cloudwatch_alarm_create: "Create CloudWatch Alarm",
  cloudwatch_alarm_delete: "Delete CloudWatch Alarm",
```

- [ ] **Step 2: Add outcome templates**

Find where `OUTCOME_TEMPLATES` is defined (it maps `ChangeType` → a default `desired_outcome` object). Add:

```typescript
  iam_user_create: {
    username: "",
    rollback_strategy: "delete_iam_user",
  },
  iam_user_delete: {
    username: "",
    rollback_strategy: "rollback_unavailable",
  },
  s3_bucket_create: {
    bucket_name: "",
    rollback_strategy: "delete_s3_bucket",
  },
  s3_bucket_delete: {
    bucket_name: "",
    rollback_strategy: "rollback_unavailable",
  },
  s3_lifecycle_configure: {
    bucket_name: "",
    rules: [],
    rollback_strategy: "restore_prior_lifecycle",
  },
  route53_zone_create: {
    zone_name: "",
    private: true,
    rollback_strategy: "delete_route53_zone",
  },
  route53_record_upsert: {
    zone_id: "",
    name: "",
    record_type: "A",
    values: [],
    ttl: 300,
    rollback_strategy: "delete_route53_record",
  },
  route53_record_delete: {
    zone_id: "",
    name: "",
    record_type: "A",
    values: [],
    ttl: 300,
    rollback_strategy: "upsert_route53_record",
  },
  rds_instance_create: {
    db_instance_identifier: "",
    engine: "mysql",
    db_instance_class: "db.t3.micro",
    master_username: "admin",
    master_password: "",
    allocated_storage: 20,
    rollback_strategy: "delete_rds_instance",
  },
  rds_instance_delete: {
    db_instance_identifier: "",
    rollback_strategy: "rollback_unavailable",
  },
  rds_snapshot_create: {
    db_instance_identifier: "",
    snapshot_identifier: "",
    rollback_strategy: "delete_rds_snapshot",
  },
  rds_snapshot_restore: {
    snapshot_identifier: "",
    db_instance_identifier: "",
    db_instance_class: "db.t3.micro",
    rollback_strategy: "delete_rds_instance",
  },
  cloudwatch_alarm_create: {
    alarm_name: "",
    metric_name: "CPUUtilization",
    namespace: "AWS/EC2",
    threshold: 80,
    comparison_operator: "GreaterThanThreshold",
    evaluation_periods: 1,
    period: 60,
    dimensions: [],
    rollback_strategy: "delete_cloudwatch_alarm",
  },
  cloudwatch_alarm_delete: {
    alarm_name: "",
    rollback_strategy: "create_cloudwatch_alarm",
  },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(ui): add labels and outcome templates for all new AWS change types"
```

---

### Task 5: Asset Quick Actions — database, storage_bucket, dns_zone

**Files:**
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Expand `database` actions**

Find the `database` entry in `ASSET_ACTIONS` (currently has `rotate_db_credentials`, `create_backup`, `provision_db_user`, `configure_db_audit`, `promote_db_replica`). Add:

```typescript
    {
      changeType: "rds_snapshot_create",
      label: "Create RDS Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Create a manual RDS snapshot of ${a.name} (${a.asset_metadata?.db_instance_identifier ?? a.name}).`,
    },
    {
      changeType: "rds_snapshot_restore",
      label: "Restore from Snapshot",
      title: (a) => `Restore ${a.name} from snapshot`,
      description: (a) => `Restore database ${a.name} from its latest manual snapshot.`,
    },
    {
      changeType: "rds_instance_delete",
      label: "Delete Instance",
      title: (a) => `Delete RDS instance ${a.name}`,
      description: (a) => `Permanently delete RDS instance ${a.asset_metadata?.db_instance_identifier ?? a.name}. Creates a safety snapshot first.`,
    },
    {
      changeType: "cloudwatch_alarm_create",
      label: "Create CloudWatch Alarm",
      title: (a) => `Monitor ${a.name}`,
      description: (a) => `Create a CloudWatch alarm on database ${a.name}.`,
    },
```

- [ ] **Step 2: Expand `storage_bucket` actions**

Find the `storage_bucket` entry. Replace its current content with:

```typescript
  storage_bucket: [
    {
      changeType: "block_s3_public_access",
      label: "Block Public Access",
      title: (a) => `Block public access on ${a.name}`,
      description: (a) => `Enable S3 Block Public Access on bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "restore_s3_public_access",
      label: "Restore Public Access",
      title: (a) => `Restore public access on ${a.name}`,
      description: (a) => `Remove S3 Block Public Access settings from bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "put_bucket_policy",
      label: "Set Bucket Policy",
      title: (a) => `Set policy on ${a.name}`,
      description: (a) => `Apply a bucket policy to ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "s3_lifecycle_configure",
      label: "Configure Lifecycle",
      title: (a) => `Configure lifecycle on ${a.name}`,
      description: (a) => `Set object expiration and transition rules on bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "s3_bucket_delete",
      label: "Delete Bucket",
      title: (a) => `Delete bucket ${a.name}`,
      description: (a) => `Empty and delete S3 bucket ${a.asset_metadata?.bucket_name ?? a.name}. Irreversible.`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup of bucket ${a.name}.`,
    },
  ],
```

- [ ] **Step 3: Expand `dns_zone` actions**

Find the `dns_zone` entry. Replace its current content with:

```typescript
  dns_zone: [
    {
      changeType: "route53_record_upsert",
      label: "Add / Update Record",
      title: (a) => `Add record to ${a.name}`,
      description: (a) => `Create or update a DNS record in zone ${a.asset_metadata?.zone_name ?? a.name}.`,
    },
    {
      changeType: "route53_record_delete",
      label: "Delete Record",
      title: (a) => `Delete record from ${a.name}`,
      description: (a) => `Delete a DNS record from zone ${a.asset_metadata?.zone_name ?? a.name}.`,
    },
    {
      changeType: "dr_dns_failover_route53",
      label: "DR Failover",
      title: (a) => `DR failover for ${a.name}`,
      description: (a) => `Swap weighted routing records to fail over traffic in zone ${a.asset_metadata?.zone_name ?? a.name}.`,
    },
  ],
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/AssetDetail.tsx
git commit -m "feat(ui): expand Quick Actions for database, storage_bucket, dns_zone"
```

---

### Task 6: Asset Quick Actions — identity, firewall, server additions

**Files:**
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Expand `identity` (IAM user) actions**

Find the `identity` entry. Replace with:

```typescript
  identity: [
    {
      changeType: "offboard_user",
      label: "Offboard User",
      title: (a) => `Offboard ${a.name}`,
      description: (a) => `Disable ${a.name} across all connected identity systems.`,
    },
    {
      changeType: "lockdown_account",
      label: "Lockdown Account",
      title: (a) => `Lockdown ${a.name}`,
      description: (a) => `Lock ${a.name} across all identity systems immediately.`,
    },
    {
      changeType: "iam_user_delete",
      label: "Delete IAM User",
      title: (a) => `Delete IAM user ${a.name}`,
      description: (a) => `Delete IAM user ${a.asset_metadata?.username ?? a.name} and all associated access keys and policies.`,
    },
    {
      changeType: "rotate_api_key",
      label: "Rotate Access Key",
      title: (a) => `Rotate access key for ${a.name}`,
      description: (a) => `Rotate the AWS IAM access key for ${a.asset_metadata?.username ?? a.name}.`,
    },
  ],
```

- [ ] **Step 2: Expand `firewall` (Security Group) actions**

Find the `firewall` entry. Replace with:

```typescript
  firewall: [
    {
      changeType: "security_group_update",
      label: "Update Rules",
      title: (a) => `Update security group ${a.name}`,
      description: (a) => `Modify inbound/outbound rules on security group ${a.asset_metadata?.security_group_id ?? a.name}.`,
    },
    {
      changeType: "snapshot_asset",
      label: "Export Rules",
      title: (a) => `Export rules from ${a.name}`,
      description: (a) => `Export current security group rules from ${a.name} for backup.`,
    },
    {
      changeType: "microsegmentation_policy",
      label: "Microsegmentation Policy",
      title: (a) => `Microsegmentation policy for ${a.name}`,
      description: (a) => `Stage a network microsegmentation policy on ${a.name}.`,
    },
  ],
```

- [ ] **Step 3: Add `snapshot_asset` and `cloudwatch_alarm_create` to `server` actions**

Find the `server` entry. After the existing `enforce_cis_benchmark` entry (the last one), add before the closing `],`:

```typescript
    {
      changeType: "cloudwatch_alarm_create",
      label: "Create CloudWatch Alarm",
      title: (a) => `Monitor ${a.name}`,
      description: (a) => `Create a CloudWatch alarm for instance ${a.asset_metadata?.instance_id ?? a.name}.`,
    },
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group for ${a.name}`,
      description: (a) => `Modify security group rules for instance ${a.asset_metadata?.instance_id ?? a.name}.`,
    },
```

- [ ] **Step 4: Expand `cloud_account` actions**

Find the `cloud_account` entry. Add:

```typescript
    {
      changeType: "iam_user_create",
      label: "Create IAM User",
      title: (a) => `Create IAM user in ${a.name}`,
      description: (a) => `Create a new IAM user in AWS account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
    {
      changeType: "s3_bucket_create",
      label: "Create S3 Bucket",
      title: (a) => `Create S3 bucket in ${a.name}`,
      description: (a) => `Create a new S3 bucket in AWS account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
    {
      changeType: "route53_zone_create",
      label: "Create Hosted Zone",
      title: (a) => `Create hosted zone in ${a.name}`,
      description: (a) => `Create a Route53 hosted zone in account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
    {
      changeType: "rds_instance_create",
      label: "Create RDS Instance",
      title: (a) => `Create RDS instance in ${a.name}`,
      description: (a) => `Launch a new RDS database instance in account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
```

- [ ] **Step 5: Expand `load_balancer` actions (placeholder pending ALB sub-project)**

Find the `load_balancer` entry. Replace with:

```typescript
  load_balancer: [
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify security group rules for load balancer ${a.name}.`,
    },
    {
      changeType: "snapshot_asset",
      label: "Snapshot Config",
      title: (a) => `Snapshot ${a.name} config`,
      description: (a) => `Capture current configuration of load balancer ${a.name}.`,
    },
    // ALB lifecycle actions (create/delete/listeners/targets) coming in ALB sub-project
  ],
```

- [ ] **Step 6: Expand `key_pair` actions**

Find the `key_pair` entry. Replace with:

```typescript
  key_pair: [
    {
      changeType: "key_pair_delete",
      label: "Delete Key Pair",
      title: (a) => `Delete key pair ${a.name}`,
      description: (a) => `Delete key pair ${a.asset_metadata?.key_name ?? a.name} from AWS. Instances using it keep their existing access.`,
    },
    {
      changeType: "rotate_ssh_keys",
      label: "Rotate Key",
      title: (a) => `Rotate key pair ${a.name}`,
      description: (a) => `Delete and recreate key pair ${a.asset_metadata?.key_name ?? a.name}.`,
    },
  ],
```

- [ ] **Step 7: Expand `endpoint` actions**

Find the `endpoint` entry. Add after the existing entries:

```typescript
    {
      changeType: "deploy_nexplane_agent",
      label: "Update Agent",
      title: (a) => `Update agent on ${a.name}`,
      description: (a) => `Deploy the latest Nexplane agent binary to endpoint ${a.name}.`,
    },
    {
      changeType: "ssm_command",
      label: "SSM Command",
      title: (a) => `Run SSM command on ${a.name}`,
      description: (a) => `Execute an AWS SSM RunShellScript command on endpoint ${a.name}.`,
    },
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/AssetDetail.tsx
git commit -m "feat(ui): expand Quick Actions for identity, firewall, server, cloud_account, load_balancer, key_pair, endpoint"
```

---

### Task 7: Visual verification

- [ ] **Step 1: Restart frontend to pick up changes**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 2: Open the browser at http://localhost:3000**

Navigate to **Change Requests → New Change Request**. Verify:
- "IAM" group appears with `Create IAM User` and `Delete IAM User`
- "S3 Storage" group appears with three types
- "DNS (Route53)" group appears with three types
- "RDS" group appears with three types
- "Observability" group appears with two types

- [ ] **Step 3: Open any `database` asset in inventory**

Verify Quick Actions panel shows: `Create RDS Snapshot`, `Restore from Snapshot`, `Delete Instance`, `Create CloudWatch Alarm` (in addition to existing ones).

- [ ] **Step 4: Open any `storage_bucket` asset**

Verify Quick Actions shows: `Block Public Access`, `Restore Public Access`, `Set Bucket Policy`, `Configure Lifecycle`, `Delete Bucket`, `Create Backup`.

- [ ] **Step 5: Open any `dns_zone` asset**

Verify Quick Actions shows: `Add / Update Record`, `Delete Record`, `DR Failover`.

- [ ] **Step 6: Open any `identity` asset**

Verify Quick Actions shows: `Offboard User`, `Lockdown Account`, `Delete IAM User`, `Rotate Access Key`.

- [ ] **Step 7: Verify selecting `iam_user_create` filters to cloud_account assets**

In New CR modal, select `Create IAM User` — asset picker should show only `cloud_account` assets.

Verify selecting `rds_instance_delete` — asset picker shows only `database` assets.

Verify selecting `route53_record_upsert` — asset picker shows only `dns_zone` assets.

- [ ] **Step 8: Final commit**

```bash
git add frontend/src/pages/CreateChangeRequest.tsx frontend/src/pages/AssetDetail.tsx frontend/src/types/api.ts
git commit -m "feat(ui): complete AWS expansion — all new change types wired in CR workflow and Quick Actions"
```

---

**Plan 4 complete. All four plans complete.**

To run the full smoke test suite:

```bash
# Quick (no RDS):
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K \
  --tailscale-auth-key tskey-auth-kTYui1NBwG11CNTRL-3PypxPACT3JRppfHm8JQ4J6yqEirTU88H

# Full including RDS (~35 min extra):
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,J,K \
  --tailscale-auth-key tskey-auth-kTYui1NBwG11CNTRL-3PypxPACT3JRppfHm8JQ4J6yqEirTU88H
```
