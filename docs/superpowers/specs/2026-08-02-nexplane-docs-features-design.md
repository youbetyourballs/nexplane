# Nexplane Docs — Feature Pages Update Design Spec

## Goal

Update existing feature pages and add new ones to reflect platform capabilities added since the last docs update. Focus on operator-facing workflows, not internal mechanics.

## New Feature Pages to Create

### `docs/features/os-upgrades.md` — OS & Application Upgrades

A feature page covering the upgrade workflow concept — why Nexplane manages upgrades (rollback guarantee, pre-flight validation, parallel upgrade pattern) and what's supported. Link to the change-types/os-upgrades.md page for CR type details.

Sections:
1. **Why managed upgrades** — snapshot before you cut, validate before you commit, stop-not-terminate for 24h rollback window
2. **Upgrade methods** — in-place vs. parallel (parallel is preferred for production; in-place for dev/test)
3. **Linux upgrades** — `os_upgrade` (in-place) and `linux_parallel_upgrade` (parallel EIP cutover)
4. **Windows migration** — `windows_parallel_migration` — inventory-driven: what gets captured (services, scheduled tasks, IIS config, env vars, certs, app config files, registry keys, DNS records, hardcoded hostname references)
5. **Kubernetes cluster upgrades** — `k8s_cluster_upgrade` — +1 minor version policy, removed API scan before upgrade
6. **Database major version upgrades** — `db_major_version_upgrade` — compatibility scan, dump/restore rollback, optional EBS snapshot
7. **Rollback** — link to platform rollback docs; explain stop-not-terminate pattern for parallel upgrades

### `docs/features/rolling-deploy.md` — Rolling Deploys with Auto-Rollback

ECS rolling deploy feature page explaining the operator workflow:
1. **The problem** — updating a task definition doesn't update the service; manual tracking of old revision for rollback is error-prone
2. **What `ecs_rolling_deploy` does** — registers new task def revision, triggers service update, polls health gates, auto-rolls back on failure
3. **Health gates** — ECS stability (runningCount == desiredCount, single deployment), ALB/NLB target health, HTTP probe
4. **Auto-rollback** — what triggers it, what it does (calls update_service back to old ARN), what it doesn't do (doesn't deregister the new task def — use `ecs_task_def_deregister` for that)
5. **Platform rollback** — operator-initiated rollback after a successful deploy (e.g., caught a regression after smoke)

## Existing Feature Pages to Update

### `docs/features/credential-rotation.md` — Add End-to-End Rotation with Fan-Out

Current page covers: DB credential rotation, SSH key rotation, API key rotation, service account rotation.

**Add section: End-to-End Rotation with Consumer Fan-Out**

`credential_rotation_fanout` closes the gap between rotating a credential and updating everything that uses it. Workflow:

1. Rotate the credential at the source (IAM key, Vault secret, database password)
2. Scan for all consumers — K8s secrets, EC2 user data, Lambda environment variables, ECS task definitions, SSM parameters — using the reference scanner
3. Update each consumer with the new value (ordered for FILO rollback)
4. Verify all consumers are using the new credential
5. Pause automatically if any consumer update fails, preserving the partial state for operator review

Rollback: unwinds in reverse order — restores each consumer, then restores the original credential at the source.

**Add section: Certificate Rotation**

`certificate_rotation` manages the full cert lifecycle including chain validation and dependent host push:

1. Scan for certificates matching target CN/SAN across all connected hosts
2. Snapshot current certificate state (pre-rotation capture)
3. Rotate — renew via step-ca, or generate new self-signed cert
4. Push updated certificate to all dependent hosts via the Nexplane Agent
5. Validate the chain and test connectivity before completing
6. Rollback: restore the snapshot (strategy C — re-issue from original CA if snapshot unavailable)

### `docs/features/identity-lifecycle.md` — Already complete

The page already covers offboard_user and onboard_user accurately. No changes needed.

### `docs/features/compliance.md` — Minor update

Add a note that the CIS v8 drift detection runs continuously and surfaces findings in the Compliance dashboard; finding-to-CR remediation is automatic for supported controls.

## Style Constraints

- New pages: 400-800 words, prose-driven, operator-focused ("what does this let you do" not "how does the executor work")
- Numbered lists for phases/workflows
- Admonition blocks for important operational notes (e.g., "stop-not-terminate: the old instance is kept for 24 hours")
- Link to change-types pages for CR type details rather than repeating parameter lists
- No internal implementation details (executor phases, boto3 calls, etc.)

## Files

**Create (nexplane-docs repo):**
- `docs/features/os-upgrades.md`
- `docs/features/rolling-deploy.md`

**Modify (nexplane-docs repo):**
- `docs/features/credential-rotation.md` — add fanout + certificate rotation sections
- `docs/features/compliance.md` — add drift-to-CR automation note
- `mkdocs.yml` — add 2 new feature nav entries
