# Nexplane Docs — New CR Types Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the major CR types added since the last docs update — OS upgrades, container operations, Active Directory tier-zero operations, and new credential rotation types.

**Architecture:** Three new pages in `docs/change-types/`, updates to two existing pages, and nav additions in `mkdocs.yml`. All content derived from executor source files and catalog JSON in the nexplane repo. No code changes — docs only.

**Tech Stack:** MkDocs Material, Markdown. Two repos: `f:/Nexplane/nexplane` (source to read) and `f:/Nexplane/nexplane-docs` (docs to write).

## Global Constraints

- All writes go to `f:/Nexplane/nexplane-docs/` — never modify the nexplane source repo
- Follow existing change-types page style: prose intro, then per-CR-type sections with description, phases (numbered list), rollback line, connector line
- Use `**Rollback:**` and `**Connector:**` bold prefixes exactly (matching existing pages)
- Do not include risk scores — they are too volatile to maintain in docs
- Do not duplicate content that already lives in feature pages — cross-link instead
- Commit from the nexplane-docs directory

---

### Task 1: OS & Application Upgrades Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/change-types/os-upgrades.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/os_upgrade.py` (or check `agent/` subdirectory — the executor may be in `nexplane_agent/`)
- `f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`
- `f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/windows_parallel_migration.py`
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/eks_cluster_upgrade.py` (or kubernetes subdirectory)
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/db_major_version_upgrade.py` (or rds subdirectory)
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/linux_parallel_upgrade.json`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/windows_parallel_migration.json`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/k8s_cluster_upgrade.json`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/db_major_version_upgrade.json`

If a file isn't found at the expected path, use `find` or `ls` to locate it.

- [ ] **Step 1: Read source files**

For each executor, identify:
1. The phase sequence (phase names or comments showing the ordered steps)
2. The ROLLBACK_CAPABILITY value
3. The key parameters (from the change_type_definition JSON or the executor's parameter handling)

- [ ] **Step 2: Write `docs/change-types/os-upgrades.md`**

Required structure:

```markdown
# OS & Application Upgrades

Nexplane orchestrates major version upgrades as tracked, approval-gated change requests with pre-flight validation and snapshot-based rollback. The upgrade does not complete until health verification passes — if it fails, the platform rolls back automatically.

## Linux In-Place Upgrade

**Change type:** `os_upgrade`

Upgrades a Linux host to the next major OS release using the distribution's standard upgrade tool (e.g., `do-release-upgrade` on Ubuntu).

1. Preflight — verify disk space, kernel compatibility, and running services
2. EBS snapshot — capture root volume before any changes
3. Run upgrade — execute distribution upgrade in unattended mode via the Nexplane Agent
4. Verify — confirm OS version, agent reconnection, and configured health checks
5. Rollback: restore the EBS root volume snapshot from step 2

**Connector:** Nexplane Agent (AWS EBS for snapshot rollback)

## Linux Parallel Upgrade

**Change type:** `linux_parallel_upgrade`

(document based on what you read in the executor — phases, rollback, connector)

**Rollback:** ...

**Connector:** ...

## Windows Parallel Migration

**Change type:** `windows_parallel_migration`

(document based on what you read in the executor)

**Rollback:** ...

**Connector:** ...

## Kubernetes Cluster Upgrade

**Change type:** `k8s_cluster_upgrade`

(document based on what you read in the executor)

!!! warning "Control plane upgrade is partially irreversible"
    Once the Kubernetes control plane etcd data is migrated to the new version format, downgrading the control plane is not supported. Node pools can be rolled back independently.

**Rollback:** ...

**Connector:** ...

## Database Major Version Upgrade

**Change type:** `db_major_version_upgrade`

(document based on what you read in the executor)

**Rollback:** ...

**Connector:** ...
```

Write the actual content by reading the executor source. Do not use placeholder text — every section must describe what the executor actually does.

- [ ] **Step 3: Add to `mkdocs.yml` nav**

In `mkdocs.yml`, find the Change Types nav section and add after the existing entries:

```yaml
    - OS & Application Upgrades: change-types/os-upgrades.md
```

Also add the `vulnerability.md` page which exists on disk but is missing from nav. Find the Change Types section and add:

```yaml
    - Vulnerability Remediation: change-types/vulnerability.md
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/change-types/os-upgrades.md mkdocs.yml
git commit -m "docs: add OS & application upgrades change types page"
```

---

### Task 2: Container Operations Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/change-types/container-operations.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/ecs_rolling_deploy.py`
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/ecs_task_def_deregister.py`
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/reference_update.py` — find the `update_ecs_task_def_env` action
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/ecs_rolling_deploy.json`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/ecs_task_def_deregister.json`

- [ ] **Step 1: Read source files**

From `ecs_rolling_deploy.py`, identify:
- The 6 phase sequence (preflight, register, deploy, stability poll, ALB gate, HTTP probe)
- Health gate parameters and what each checks
- Auto-rollback trigger conditions
- Module-level `rollback()` function behavior (platform rollback after successful deploy)

From `ecs_task_def_deregister.py`, identify:
- What it calls (deregister_task_definition)
- ROLLBACK_CAPABILITY value

From `reference_update.py`, find the `update_ecs_task_def_env` handler:
- What it does (registers new task def with updated env vars, does NOT call update_service)
- When to use it vs. `ecs_rolling_deploy`

- [ ] **Step 2: Write `docs/change-types/container-operations.md`**

```markdown
# Container Operations

ECS change types manage task definition revisions and service deployments on Amazon ECS.

## ECS Rolling Deploy

**Change type:** `ecs_rolling_deploy`

Registers a new ECS task definition revision (image tag change, environment variable update, or both), drives a rolling service update, and polls health gates before completing. If any health gate fails, the service is automatically rolled back to the previous task definition revision.

**Phases:**

1. Preflight — validate the ECS service and cluster exist; record the current task definition ARN for rollback
2. Register — create a new task definition revision with the specified image tag and/or environment variable changes
3. Deploy — call `UpdateService` with the new task definition
4. Stability poll — wait for `runningCount == desiredCount` with a single active deployment
5. ALB/NLB health gate *(optional)* — wait for all targets in the specified target group to report healthy
6. HTTP probe gate *(optional)* — GET the specified health check URL and verify the expected HTTP status code

If phase 4, 5, or 6 fails, the executor automatically calls `UpdateService` back to the previous task definition ARN.

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `service_arn` | Yes | ECS service ARN or name |
| `cluster` | Yes | Cluster name or ARN |
| `image_tag` | No* | Full image string (e.g., `nginx:1.27`) |
| `env_var_overrides` | No* | Array of `{key, old_value, new_value}` — old_value is verified before update |
| `container_name` | No | Required when image_tag is set and the task definition has more than one container |
| `target_group_arn` | No | Enables ALB/NLB health gate |
| `health_check_url` | No | Enables HTTP probe gate |
| `health_check_expected_status` | No | Expected HTTP status code (default: 200) |
| `stability_timeout_seconds` | No | Stability poll timeout (default: 300) |
| `health_timeout_seconds` | No | Timeout for ALB and HTTP gates (default: 60) |

*At least one of `image_tag` or `env_var_overrides` is required.

**Rollback:** Call `UpdateService` back to the previous task definition ARN. The new task definition revision is left registered — use `ecs_task_def_deregister` to clean it up.

**Connector:** AWS

---

## ECS Task Definition Deregister

**Change type:** `ecs_task_def_deregister`

Deregisters an ECS task definition revision. Typically used to clean up a failed-deploy task definition after auto-rollback has restored the service.

!!! warning "Irreversible"
    ECS has no API to re-register a deregistered task definition revision. This operation cannot be rolled back.

**Parameters:** `task_def_arn` (required), `region` (optional)

**Rollback:** Not available.

**Connector:** AWS

---

## Update ECS Task Definition Environment Variables

**Change type:** `update_ecs_task_def_env`

Registers a new ECS task definition revision with updated environment variables. **Does not call `UpdateService`** — the running service continues on the old revision. Use this when only the new task definition ARN is needed (for example, as part of a `credential_rotation_fanout` that updates multiple consumers).

To deploy the new revision to a live service, use `ecs_rolling_deploy` instead.

**Rollback:** Register a new revision restoring the original environment variables.

**Connector:** AWS
```

- [ ] **Step 3: Add to `mkdocs.yml` nav**

Add to the Change Types nav section:

```yaml
    - Container Operations: change-types/container-operations.md
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/change-types/container-operations.md mkdocs.yml
git commit -m "docs: add container operations change types page"
```

---

### Task 3: Active Directory Operations Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/change-types/active-directory.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/active_directory/` — list and read: `ad_domain_functional_level_upgrade.py`, `ad_trust_create.py`, `ad_gpo_deploy.py`, `ad_pso_manage.py`, `ad_stale_computer_cleanup.py`, `ad_dc_parallel_upgrade.py`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/ad_domain_functional_level_upgrade.json` (and the other 5 JSON files)

- [ ] **Step 1: Read source files**

For each of the 6 executors:
1. Identify the phase sequence
2. Check ROLLBACK_CAPABILITY value
3. Note key parameters from the JSON definition

- [ ] **Step 2: Write `docs/change-types/active-directory.md`**

```markdown
# Active Directory Operations

Tier-zero Active Directory operations affect domain-wide security posture and require domain admin credentials. All operations are logged and require approval before execution.

## Domain Functional Level Upgrade

**Change type:** `ad_domain_functional_level_upgrade`

(describe what it does, based on source)

!!! warning "Irreversible"
    Raising the forest or domain functional level cannot be undone. Ensure all domain controllers support the target functional level before proceeding.

**Rollback:** Not available.

**Connector:** Active Directory

---

## Forest/Domain Trust

**Change type:** `ad_trust_create`

(describe what it does, phases, rollback)

**Rollback:** ...

**Connector:** Active Directory

---

## Group Policy Deployment

**Change type:** `ad_gpo_deploy`

(describe what it does — pilot OU scoping before domain-wide rollout, etc.)

**Rollback:** ...

**Connector:** Active Directory

---

## Fine-Grained Password Policy

**Change type:** `ad_pso_manage`

(describe create/update/delete operations)

**Rollback:** ...

**Connector:** Active Directory

---

## Stale Computer Account Cleanup

**Change type:** `ad_stale_computer_cleanup`

(describe discovery, disable-first lifecycle, dry_run mode)

**Rollback:** ...

**Connector:** Active Directory

---

## Domain Controller Parallel Upgrade

**Change type:** `ad_dc_parallel_upgrade`

(describe Microsoft-prescribed sequence: promote new DC → replicate → FSMO transfer → demote old DC)

**Rollback:** ...

**Connector:** Active Directory
```

Write all section content from what you read in the executor source — no placeholder text.

- [ ] **Step 3: Add to `mkdocs.yml` nav**

Add to the Change Types nav section:

```yaml
    - Active Directory: change-types/active-directory.md
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/change-types/active-directory.md mkdocs.yml
git commit -m "docs: add Active Directory operations change types page"
```

---

### Task 4: Update Credentials Page with Fanout and Certificate Rotation

**Files:**
- Modify: `f:/Nexplane/nexplane-docs/docs/change-types/credentials.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/credential_rotation_fanout.py` (may be in a different path — search for `credential_rotation_fanout`)
- `f:/Nexplane/nexplane/backend/app/connectors/executors/` — find `certificate_rotation.py`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/credential_rotation_fanout.json`
- `f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/certificate_rotation.json`

- [ ] **Step 1: Read source files**

For `credential_rotation_fanout.py`:
1. Phase sequence (rotate → scan → update consumers → verify → pause on failure)
2. ROLLBACK_CAPABILITY value
3. Supported credential sources (IAM key, Vault secret, etc.)

For `certificate_rotation.py`:
1. Phase sequence
2. ROLLBACK_CAPABILITY value
3. Supported CA backends (step-ca, self-signed, etc.)

- [ ] **Step 2: Append two sections to `credentials.md`**

Read the current `f:/Nexplane/nexplane-docs/docs/change-types/credentials.md` first, then append these two sections at the end:

```markdown
## End-to-End Rotation with Consumer Fan-Out

**Change type:** `credential_rotation_fanout`

Rotates a credential at its source and automatically updates all infrastructure components that reference it — closing the gap between "the key was rotated" and "everything using the key has the new value."

1. Rotate the credential at the source (IAM access key, Vault secret, database password)
2. Scan for consumers — Kubernetes secrets, EC2 user data, Lambda environment variables, ECS task definitions, SSM parameters — using the reference scanner
3. Update each consumer with the new credential value (ordered for FILO rollback compatibility)
4. Verify all consumers respond with the new credential
5. Pause automatically if any consumer update fails, preserving the partial state for operator review

**Rollback:** Unwind consumers in reverse order, then restore the original credential at the source. If auto-rollback fails midway, the execution result records which consumers were updated so operators can restore manually.

**Connector:** AWS (source credential) + Nexplane Agent (consumer updates on hosts)

---

## Certificate Rotation

**Change type:** `certificate_rotation`

Rotates TLS certificates across all dependent hosts with chain validation before completing.

(write phases based on what you read in the executor source)

**Rollback:** (write based on ROLLBACK_CAPABILITY and rollback logic in source)

**Connector:** step-ca (for CA-signed certs) or Nexplane Agent (for self-signed certs)
```

Write all content from what you read in the source — no placeholder text.

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/change-types/credentials.md
git commit -m "docs: add credential_rotation_fanout and certificate_rotation to credentials page"
```
