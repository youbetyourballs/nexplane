# Nexplane Docs — Feature Pages Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new feature pages (OS & Application Upgrades, Rolling Deploys) and update two existing feature pages (credential-rotation, compliance) to reflect platform capabilities added since the last docs update.

**Architecture:** Feature pages are operator-focused narrative docs — they explain what a workflow does and why, linking to change-types pages for CR type details. They do not repeat parameter lists.

**Tech Stack:** MkDocs Material, Markdown. Two repos: `f:/Nexplane/nexplane` (source to read for accuracy) and `f:/Nexplane/nexplane-docs` (docs to write).

## Global Constraints

- All writes go to `f:/Nexplane/nexplane-docs/` — never modify the nexplane source repo
- Feature pages are 400-800 words, prose-driven, operator-focused
- Do not repeat parameter tables from change-types pages — link to them instead: `[ecs_rolling_deploy](../change-types/container-operations.md)`
- Use admonition blocks for important operational notes: `!!! note "Title"` or `!!! warning "Title"`
- Numbered lists for phase/workflow sequences
- Commit from the nexplane-docs directory

---

### Task 1: OS & Application Upgrades Feature Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/features/os-upgrades.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo — for accuracy check only, not to copy):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py` — understand the parallel upgrade pattern (stop-not-terminate)
- `f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/windows_parallel_migration.py` — understand what gets inventoried and transferred

**Cross-link target:** `../change-types/os-upgrades.md` (written in the CR types sub-project)

- [ ] **Step 1: Read source files**

From `linux_parallel_upgrade.py`, confirm:
- The stop-not-terminate pattern (old instance stopped, not terminated, kept for rollback window)
- The rollback window duration

From `windows_parallel_migration.py`, confirm the inventory list:
- What artifacts are captured from the source (services, scheduled tasks, IIS config, env vars, certs, app config files, registry keys, DNS records, hardcoded hostname references)

- [ ] **Step 2: Write `docs/features/os-upgrades.md`**

```markdown
# OS & Application Upgrades

Nexplane orchestrates major version upgrades as audited, approval-gated change requests. The core guarantee: the platform takes a snapshot or preserves the old instance before making any irreversible change, and the rollback path is tested before the upgrade is declared complete.

## Why Managed Upgrades

Unmanaged upgrades fail silently. An in-place upgrade that leaves the OS half-migrated, or a new instance that came up missing a scheduled task, typically isn't discovered until something breaks in production. Nexplane enforces:

- **Pre-flight validation** before any change is made
- **State capture** (EBS snapshot or preserved instance) before cutting over
- **Health verification** before completing — failed verification triggers automatic rollback
- **24-hour rollback window** for parallel upgrades (old instance stopped, not terminated)

## Upgrade Methods

### In-Place (Linux)

`os_upgrade` runs the distribution's standard upgrade tool (`do-release-upgrade` on Ubuntu) on the existing host. Suitable for dev/staging. For production, prefer the parallel approach.

Rollback: EBS root volume swap from the pre-upgrade snapshot.

### Parallel Upgrade (Linux)

`linux_parallel_upgrade` provisions a new instance at the target OS version, transfers state, and performs an EIP cutover. The old instance is **stopped but not terminated** and remains available for rollback for 24 hours.

This is the preferred method for production Linux hosts because:
- The old instance is preserved intact and can be started immediately on rollback
- Cutover is a single EIP reassignment — fast and reversible
- The new instance can be fully validated before the cutover

### Parallel Migration (Windows)

`windows_parallel_migration` handles Windows hosts where in-place upgrade is too risky. It inventories the source host comprehensively before provisioning a new instance:

- Windows services and their startup configuration
- Scheduled tasks
- IIS sites, application pools, and bindings
- Environment variables
- Installed certificates
- Application configuration files
- Registry keys
- DNS records referencing the old hostname
- Hardcoded hostname references in config files

Each artifact is transferred to the new instance. DNS cutover completes the migration.

Rollback: revert DNS to point back to the old instance (kept running during migration).

## Kubernetes Cluster Upgrade

`k8s_cluster_upgrade` upgrades EKS, GKE, AKS, or kubeadm clusters. The platform enforces a maximum of +1 minor version per upgrade (e.g., 1.28 → 1.29, not 1.28 → 1.31) and scans for removed API usage before starting.

!!! warning "Control plane partial irreversibility"
    Once the control plane etcd data migrates to the new version format, the control plane cannot be downgraded. Node pools can be rolled back independently.

## Database Major Version Upgrades

`db_major_version_upgrade` handles PostgreSQL, MySQL, and MongoDB major version upgrades. A compatibility scan runs before the upgrade starts. Rollback uses `pg_dump`/`mysqldump`/`mongodump` restore from a pre-upgrade dump, with an optional EBS snapshot as a faster fallback.

## See Also

- [OS & Application Upgrades](../change-types/os-upgrades.md) — full CR type reference with parameters
```

- [ ] **Step 3: Add to `mkdocs.yml` nav**

In the Features section, add:

```yaml
    - OS & Application Upgrades: features/os-upgrades.md
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/features/os-upgrades.md mkdocs.yml
git commit -m "docs: add OS & application upgrades feature page"
```

---

### Task 2: Rolling Deploys Feature Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/features/rolling-deploy.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo — for accuracy check):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/ecs_rolling_deploy.py` — understand the health gate sequence and auto-rollback trigger

**Cross-link target:** `../change-types/container-operations.md`

- [ ] **Step 1: Read source file**

From `ecs_rolling_deploy.py`, confirm:
- The three health gates and what each checks
- What exactly auto-rollback does (calls update_service back to old_task_def_arn; does NOT deregister the new task def)
- What platform rollback does (same update_service call, triggered by operator)

- [ ] **Step 2: Write `docs/features/rolling-deploy.md`**

```markdown
# Rolling Deploys with Auto-Rollback

Nexplane orchestrates ECS rolling deploys as tracked change requests with multi-gate health checks and automatic rollback if any gate fails.

## The Problem

Registering a new ECS task definition revision doesn't deploy it — `UpdateService` must be called separately. Tracking the old revision ARN for rollback, watching deployment health, and running `UpdateService` again if health fails is manual work that is easy to skip under pressure.

`ecs_rolling_deploy` closes this gap: it registers the new revision, triggers the service update, polls three health gates, and rolls back automatically if any fails.

## Health Gates

The executor polls gates in sequence. All three can be enabled simultaneously; earlier gates must pass before later ones are checked.

**1. ECS Stability**

Polls `DescribeServices` every 10 seconds. Passes when `runningCount == desiredCount` and there is exactly one active deployment. If the stability timeout is exceeded (default: 300s), the service is rolled back automatically.

**2. ALB/NLB Target Health** *(optional)*

When `target_group_arn` is provided, polls `DescribeTargetHealth` every 10 seconds. Passes when all registered targets report `healthy`. Enable this when the ECS service sits behind a load balancer.

**3. HTTP Probe** *(optional)*

When `health_check_url` is provided, GETs the URL and checks for the expected HTTP status code (default: 200). Useful for verifying application-level health beyond ECS service stability.

## Auto-Rollback

If any health gate times out or returns an unexpected result, the executor immediately calls `UpdateService` back to the previous task definition ARN. The execution result records:

- `rolled_back: true`
- `rollback_reason` — which gate failed and why
- `old_task_def_arn` and `new_task_def_arn`

The new task definition revision is left registered after auto-rollback — it is not automatically deregistered. Use [`ecs_task_def_deregister`](../change-types/container-operations.md#ecs-task-definition-deregister) to clean it up once you've confirmed it's no longer needed.

## Platform Rollback

If a deploy completes successfully but a regression is later discovered, operators can trigger a platform rollback from the CR detail view. This calls `UpdateService` back to the task definition ARN recorded at the start of the deploy.

## See Also

- [Container Operations](../change-types/container-operations.md) — full CR type reference with all parameters
```

- [ ] **Step 3: Add to `mkdocs.yml` nav**

In the Features section, add:

```yaml
    - Rolling Deploys: features/rolling-deploy.md
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/features/rolling-deploy.md mkdocs.yml
git commit -m "docs: add rolling deploys feature page"
```

---

### Task 3: Update Credential Rotation Feature Page

**Files:**
- Modify: `f:/Nexplane/nexplane-docs/docs/features/credential-rotation.md`

**Source files to read (nexplane repo — for accuracy check):**
- Find and read the `credential_rotation_fanout` executor
- Find and read the `certificate_rotation` executor

- [ ] **Step 1: Read source files**

For `credential_rotation_fanout`: confirm the phase sequence and what happens when a consumer update fails (auto-pause, not auto-rollback).

For `certificate_rotation`: confirm the CA backends supported (step-ca, self-signed), the chain validation step, and the rollback strategy.

- [ ] **Step 2: Read the current `credential-rotation.md`**

Read `f:/Nexplane/nexplane-docs/docs/features/credential-rotation.md` in full. Find the end of the file.

- [ ] **Step 3: Append two new sections**

Append to the end of `credential-rotation.md`:

```markdown
## End-to-End Rotation with Consumer Fan-Out

**Change type:** `credential_rotation_fanout`

Standard credential rotation stops at the source — the key is rotated, but every service that was using it still has the old value. `credential_rotation_fanout` closes this gap.

After rotating the credential at the source, the executor runs a reference scan to discover every infrastructure component that holds the old value: Kubernetes secrets, EC2 instance user data, Lambda environment variables, ECS task definitions, and SSM parameters. It then updates each consumer with the new value in dependency order.

If any consumer update fails, execution pauses automatically — the operator can review the partial state, fix the failing consumer, and resume. The FILO rollback stack unwinds consumers in reverse order before restoring the original credential at the source.

## Certificate Rotation

**Change type:** `certificate_rotation`

Rotates TLS certificates across all dependent hosts with chain validation before completing.

(write based on what you read in the executor — phases, CA backends, rollback strategy)
```

Write the certificate rotation section from the source — no placeholder text.

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/features/credential-rotation.md
git commit -m "docs: add credential fanout and certificate rotation to credential rotation feature page"
```
