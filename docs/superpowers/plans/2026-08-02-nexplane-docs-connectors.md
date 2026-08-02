# Nexplane Docs — Connector Page Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update seven connector pages to reflect capabilities added since the last docs update. Targeted edits only — do not rewrite sections that are accurate.

**Architecture:** Seven existing markdown files in `docs/connectors/` get new sections or entries appended. No new files, no nav changes — all seven connectors are already in the nav.

**Tech Stack:** MkDocs Material, Markdown. Two repos: `f:/Nexplane/nexplane` (source to read) and `f:/Nexplane/nexplane-docs` (docs to write).

## Global Constraints

- All writes go to `f:/Nexplane/nexplane-docs/` — never modify the nexplane source repo
- Follow the existing connector page style: capability bullet lists, credential requirements table, links to change-types pages
- Connector pages list CR types with one-line descriptions — detail lives in change-types pages
- Do not rewrite sections that aren't being updated
- Commit from the nexplane-docs directory

---

### Task 1: AWS Connector — Add ECS Section

**Files:**
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/aws.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/ecs_rolling_deploy.py` — confirm the AWS API calls made (for credential requirements)
- `f:/Nexplane/nexplane/backend/app/connectors/executors/aws/ecs_task_def_deregister.py` — same
- `f:/Nexplane/nexplane/backend/app/connectors/catalog/aws.json` — find the ecs_rolling_deploy, ecs_task_def_deregister, update_ecs_task_def_env entries

- [ ] **Step 1: Read current `aws.md`**

Read `f:/Nexplane/nexplane-docs/docs/connectors/aws.md` in full. Find where to append the ECS section (after the existing compute/EC2 sections, before or after RDS).

- [ ] **Step 2: Append ECS section**

Append to `aws.md`:

```markdown
## ECS

| Change Type | Description |
|-------------|-------------|
| `ecs_rolling_deploy` | Register a new task definition revision and drive a rolling service update with ECS stability, ALB health, and HTTP probe gates. Auto-rolls back to the previous revision if any gate fails. |
| `ecs_task_def_deregister` | Deregister a task definition revision. Irreversible — ECS has no re-register API. |
| `update_ecs_task_def_env` | Register a new task definition revision with updated environment variables. Does not update the running service — use `ecs_rolling_deploy` for live deploys. |

**Required IAM permissions:**
- `ecs:DescribeServices`
- `ecs:DescribeTaskDefinition`
- `ecs:RegisterTaskDefinition`
- `ecs:UpdateService`
- `ecs:DeregisterTaskDefinition`
- `elasticloadbalancing:DescribeTargetHealth` *(required only if using the ALB health gate)*

See [Container Operations](../change-types/container-operations.md) for full parameter reference.
```

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/connectors/aws.md
git commit -m "docs: add ECS section to AWS connector page"
```

---

### Task 2: Active Directory Connector — Add Tier-Zero Section

**Files:**
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/active-directory.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/connectors/executors/active_directory/` — list the directory to confirm which executors exist

- [ ] **Step 1: Read current `active-directory.md`**

Read the full file. Find where to append the tier-zero section (at the end, after existing sections).

- [ ] **Step 2: Append tier-zero section**

```markdown
## Tier-Zero Operations

Tier-zero operations affect domain-wide security posture and require domain admin credentials. All are subject to approval tier 2 (high-risk) by default.

| Change Type | Description |
|-------------|-------------|
| `ad_domain_functional_level_upgrade` | Raise the forest or domain functional level. **Irreversible** — cannot be downgraded once applied. |
| `ad_trust_create` | Create a forest or domain trust with Kerberos validation. |
| `ad_gpo_deploy` | Deploy a Group Policy Object with optional pilot OU scoping before domain-wide rollout. |
| `ad_pso_manage` | Create, update, or delete a Fine-Grained Password Settings Object. |
| `ad_stale_computer_cleanup` | Discover and disable computer accounts with no recent logon. Supports `dry_run` mode. |
| `ad_dc_parallel_upgrade` | Upgrade a domain controller using the Microsoft-prescribed sequence: promote new DC, replicate, transfer FSMO roles, demote old DC. |

See [Active Directory Operations](../change-types/active-directory.md) for full parameter reference.
```

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/connectors/active-directory.md
git commit -m "docs: add tier-zero operations section to Active Directory connector page"
```

---

### Task 3: Kubernetes, Database Connectors — Minor Additions

**Files:**
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/kubernetes.md`
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/postgres.md`
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/redis.md`
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/mongodb.md`
- Modify: `f:/Nexplane/nexplane-docs/docs/connectors/step-ca.md`

- [ ] **Step 1: Read all five files**

Read each file in full to find the appropriate place to add the new entries.

- [ ] **Step 2: Update `kubernetes.md`**

Find the list of supported change types or capabilities and add:

```markdown
| `k8s_cluster_upgrade` | Major version upgrade for EKS, GKE, AKS, and self-managed kubeadm clusters. Enforces +1 minor version maximum per upgrade. Scans for removed API usage before starting. |
```

If no table exists, add a section:

```markdown
## Cluster Upgrades

`k8s_cluster_upgrade` performs major version upgrades on EKS, GKE, AKS, and self-managed kubeadm clusters. The platform enforces a maximum of +1 minor version per upgrade and scans for deprecated API usage before starting. Control plane upgrade is partially irreversible; node pools can be rolled back independently.

See [OS & Application Upgrades](../change-types/os-upgrades.md#kubernetes-cluster-upgrade) for full parameter reference.
```

- [ ] **Step 3: Update `postgres.md`, `redis.md`, `mongodb.md`**

For each database connector page, add a note about major version upgrade support. Add to the capabilities section or at the end of the page:

For `postgres.md`:
```markdown
**Major version upgrades:** `db_major_version_upgrade` supports PostgreSQL major version upgrades with pre-upgrade compatibility scan and `pg_dump`-based rollback. See [OS & Application Upgrades](../change-types/os-upgrades.md#database-major-version-upgrade).
```

For `redis.md`:
```markdown
**Major version upgrades:** `db_major_version_upgrade` supports Redis major version upgrades. See [OS & Application Upgrades](../change-types/os-upgrades.md#database-major-version-upgrade).
```

For `mongodb.md`:
```markdown
**Major version upgrades:** `db_major_version_upgrade` supports MongoDB major version upgrades with `mongodump`-based rollback. See [OS & Application Upgrades](../change-types/os-upgrades.md#database-major-version-upgrade).
```

- [ ] **Step 4: Update `step-ca.md`**

Add to the capabilities or integration section:

```markdown
**Certificate rotation:** step-ca is the CA backend for `certificate_rotation` change requests. Nexplane calls the step-ca API to renew certificates as part of the rotation workflow, then pushes the renewed certificate to dependent hosts via the Nexplane Agent. See [Certificate Rotation](../change-types/credentials.md#certificate-rotation).
```

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/connectors/kubernetes.md docs/connectors/postgres.md docs/connectors/redis.md docs/connectors/mongodb.md docs/connectors/step-ca.md
git commit -m "docs: add upgrade and certificate rotation notes to connector pages"
```
