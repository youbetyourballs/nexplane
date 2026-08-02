# Nexplane Docs — New CR Types Documentation Design Spec

## Goal

Document the CR types added since the last docs update. The existing change-types section covers ~60 types; the platform now has significantly more, including several high-value types operators will actively use. Focus on operator-facing CR types with rollback, not internal scaffolding types.

## New Pages to Create

### `docs/change-types/os-upgrades.md` — OS & Application Upgrades

These CR types handle major version upgrades with pre-flight validation, snapshot-based rollback, and post-upgrade verification.

**CR types to document:**

| CR Type | Description |
|---|---|
| `os_upgrade` | Linux in-place upgrade via do-release-upgrade. Preflight → EBS snapshot → upgrade → verify. Rollback: EBS root volume swap. |
| `linux_parallel_upgrade` | Provision new instance at target OS, transfer state, EIP cutover, stop-not-terminate old for 24h rollback window. |
| `windows_parallel_migration` | Provision new Windows instance, inventory source (services + scheduled tasks + IIS + env vars + certs + app config + registry + DNS records), transfer all artifacts, cutover. |
| `k8s_cluster_upgrade` | EKS/GKE/AKS/kubeadm +1 minor version enforced, removed API scan, rolling node pool upgrade. Rollback: control plane partial (irreversible after etcd migration); node pools reversible. |
| `db_major_version_upgrade` | PostgreSQL/MySQL/MongoDB major version upgrade. Pre-upgrade compatibility scan, pg_dump/restore-based rollback, optional EBS snapshot. |

Each entry: description, phases (numbered list), rollback strategy, connector, key parameters.

### `docs/change-types/container-operations.md` — Container Operations

ECS-specific operations for task definition management and rolling deploys.

| CR Type | Description |
|---|---|
| `ecs_rolling_deploy` | Register new task def revision (image tag or env var change), drive rolling service update, poll ECS stability + optional ALB health gate + optional HTTP probe. Auto-rollback to previous task def ARN if any gate fails. |
| `ecs_task_def_deregister` | Deregister a task definition revision. Irreversible — ECS has no re-register API. Use after confirming a failed deploy's new task def is no longer needed. |
| `update_ecs_task_def_env` | Register a new task def revision with updated env vars only. Does NOT trigger a service update — used when only the task def ARN is needed (e.g., credential rotation fanout). |

### `docs/change-types/active-directory.md` — Active Directory Operations

Tier-zero AD operations: all require DC connectivity and domain admin credentials.

| CR Type | Description |
|---|---|
| `ad_domain_functional_level_upgrade` | Raise forest/domain functional level. Irreversible. |
| `ad_trust_create` | Create a forest or domain trust with Kerberos validation. |
| `ad_gpo_deploy` | Deploy a Group Policy Object, optionally scoped to a pilot OU before domain-wide rollout. |
| `ad_pso_manage` | Create, update, or delete a Fine-Grained Password Settings Object. |
| `ad_stale_computer_cleanup` | Discover and disable computer accounts with no recent logon. Disable-first lifecycle with dry_run mode. |
| `ad_dc_parallel_upgrade` | Microsoft-prescribed DC upgrade: promote new DC, replicate, transfer FSMO roles, demote old DC. |
| `ad_forest_restore` | Restore an AD DC from a backup artifact. |

## Updates to Existing Pages

### `docs/change-types/credentials.md` — Add two new sections

**`credential_rotation_fanout`** — End-to-end credential rotation with consumer discovery:
1. Rotate the credential at the source (IAM key, Vault secret, etc.)
2. Scan for all consumers referencing the old credential (K8s secrets, EC2 user data, Lambda env vars, ECS task defs, SSM parameters)
3. Update each consumer with the new credential (FILO ordered)
4. Verify all consumers
5. Auto-pause if any consumer update fails

Rollback: FILO unwind — restore consumers in reverse order, then restore original credential.

**`certificate_rotation`** — Certificate lifecycle with chain validation:
1. Scan for certificates matching the target CN/SAN
2. Snapshot current cert state
3. Rotate (renew via step-ca or generate new self-signed)
4. Push updated cert to dependent hosts
5. Validate chain and connectivity
6. Rollback strategy C: restore snapshot or re-issue from original CA

### `docs/change-types/database.md` — Add entry

Add `db_major_version_upgrade` entry pointing to the new os-upgrades.md page (or inline — prefer inline since it's database-focused).

### `docs/change-types/identity.md` — Already has offboard_user and onboard_user

No changes needed — identity-lifecycle.md covers these adequately.

## mkdocs.yml Nav Updates

Add to Change Types section:
```yaml
- OS & Application Upgrades: change-types/os-upgrades.md
- Container Operations: change-types/container-operations.md
- Active Directory: change-types/active-directory.md
```

Also add `vulnerability.md` to nav (it exists on disk but is missing from nav).

## Style Constraints

- Same style as existing change-types pages: prose intro, then per-type sections with description + numbered phases + rollback line
- Rollback listed as: `**Rollback:** <one sentence>`
- Connector listed as: `**Connector:** <name>`
- Risk scores omitted (too volatile to keep current in docs)
- Do not duplicate content already in the features pages — link to them instead

## Files

**Create (nexplane-docs repo):**
- `docs/change-types/os-upgrades.md`
- `docs/change-types/container-operations.md`
- `docs/change-types/active-directory.md`

**Modify (nexplane-docs repo):**
- `docs/change-types/credentials.md` — add credential_rotation_fanout + certificate_rotation sections
- `docs/change-types/database.md` — add db_major_version_upgrade entry
- `mkdocs.yml` — add 3 new nav entries; add vulnerability.md to nav
