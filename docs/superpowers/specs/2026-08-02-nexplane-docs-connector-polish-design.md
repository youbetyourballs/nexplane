# Nexplane Docs — Connector Page Polish Design Spec

## Goal

Update existing connector pages to reflect capabilities added since the last docs update. Scope is targeted: update only what's materially changed or missing, not a full rewrite.

## Connector Pages to Update

### `docs/connectors/aws.md` — Add ECS section

The AWS connector page covers EC2, IAM, S3, RDS, CloudWatch, Route53, security groups. It is missing ECS entirely.

**Add section: ECS**

Capabilities:
- `ecs_rolling_deploy` — register new task def revision and drive rolling service update with health gates and auto-rollback
- `ecs_task_def_deregister` — deregister a task definition revision (irreversible)
- `update_ecs_task_def_env` — register a new task def revision with updated environment variables (does not trigger a service update)

Credential requirements: `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition`, `ecs:UpdateService`, `ecs:DeregisterTaskDefinition`, plus `elasticloadbalancing:DescribeTargetHealth` if ALB gate is used.

### `docs/connectors/active-directory.md` — Add tier-zero operations section

The Active Directory connector page covers basic user/group operations and DC restore. It is missing the tier-zero operations added in 2026.

**Add section: Tier-Zero Operations**

List the six tier-zero CR types with one-line descriptions:
- `ad_domain_functional_level_upgrade` — raise forest/domain functional level (irreversible)
- `ad_trust_create` — create forest or domain trust with Kerberos validation
- `ad_gpo_deploy` — deploy a Group Policy Object with optional pilot OU scoping
- `ad_pso_manage` — create, update, or delete Fine-Grained Password Settings Objects
- `ad_stale_computer_cleanup` — discover and disable computer accounts with no recent logon
- `ad_dc_parallel_upgrade` — Microsoft-prescribed DC upgrade (promote new → replicate → FSMO transfer → demote old)

Add note: tier-zero operations require domain admin credentials and are subject to approval tier 2 (high-risk) by default. The `ad_domain_functional_level_upgrade` type is marked irreversible and cannot be rolled back.

### `docs/connectors/kubernetes.md` — Add cluster upgrade

**Add entry:** `k8s_cluster_upgrade` — major version upgrade for EKS, GKE, AKS, and self-managed kubeadm clusters. Enforces +1 minor version maximum per upgrade, scans for removed API usage before starting, performs rolling node pool upgrade. Control plane upgrade is partially irreversible after etcd migration completes.

### `docs/connectors/postgres.md`, `connectors/redis.md`, `connectors/mongodb.md` — Add major version upgrade

Each page should note that `db_major_version_upgrade` supports this engine. One line per page pointing to the change-types/os-upgrades.md page.

### `docs/connectors/step-ca.md` — Add certificate rotation

Add a note that step-ca is used as the CA backend for `certificate_rotation` CR type — Nexplane calls the step-ca API to renew certificates as part of the rotation workflow.

## Pages That Need No Changes

The following connector pages are accurate and complete as-is:
- All cloud connectors (GCP, Azure, OCI) — capability descriptions are accurate
- Identity connectors (Okta, LDAP, Entra ID, etc.) — accurate
- Security tool connectors (CrowdStrike, Defender, SentinelOne, etc.) — accurate
- IaC connectors (Terraform, Ansible, Helm, CloudFormation, etc.) — accurate
- Observability connectors (Datadog, Splunk, PagerDuty, etc.) — accurate

## Style Constraints

- Follow existing connector page style: capability list, credential requirements, link to change-types
- One-line descriptions per CR type in connector pages — detail lives in change-types pages
- Do not rewrite sections that aren't being updated

## Files

**Modify (nexplane-docs repo):**
- `docs/connectors/aws.md` — add ECS section
- `docs/connectors/active-directory.md` — add tier-zero operations section
- `docs/connectors/kubernetes.md` — add k8s_cluster_upgrade entry
- `docs/connectors/postgres.md` — add db_major_version_upgrade note
- `docs/connectors/redis.md` — add db_major_version_upgrade note
- `docs/connectors/mongodb.md` — add db_major_version_upgrade note
- `docs/connectors/step-ca.md` — add certificate_rotation note
