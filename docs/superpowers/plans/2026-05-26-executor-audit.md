# Executor Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a backlog-feeding report that classifies every executor by implementation status and maps each connector domain against existing smoke test coverage.

**Architecture:** Pure static analysis — read executor files and the smoke test file, classify, write a markdown report. No production code changes, no tests, no migrations. The deliverable is `docs/superpowers/specs/2026-05-26-executor-audit-report.md`.

**Tech Stack:** File reading, grep, text analysis. No runtime dependencies.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Read (many) | `backend/app/connectors/executors/**/*.py` | Source of truth for implementation status |
| Read | `backend/tests/smoke/test_feature_smoke_live.py` | Source of truth for smoke coverage |
| Create | `docs/superpowers/specs/2026-05-26-executor-audit-report.md` | The report |

---

## Known Executor Domains (75 total)

`active_directory`, `ansible`, `ansible_local`, `aws`, `azure`, `azure_ad`, `bicep`, `bind_dns`, `checkov`, `chef_inspec`, `cloudflare`, `cloudformation`, `crowdstrike`, `datadog`, `defender_endpoint`, `elastic`, `entra_id`, `falco`, `freeipa`, `gcp`, `gitea`, `github`, `gitlab`, `google_workspace`, `hashicorp_vault`, `helm`, `identity`, `infisical`, `intune`, `jfrog`, `jira`, `keycloak`, `kubernetes`, `laps`, `ldap`, `mongodb`, `nessus`, `nexplane_agent`, `oci`, `offboard_user`, `okta`, `onboard_user`, `openvas`, `opnsense`, `pagerduty`, `paloalto`, `postgres`, `pulumi`, `qualys`, `redis`, `runzero`, `saltstack`, `santa_sync_server`, `sccm`, `sentinelone`, `servicenow`, `slack`, `smtp`, `snyk`, `splunk`, `ssh`, `step_ca`, `tailscale`, `teleport`, `tenable`, `terraform`, `terraform_local`, `vault`, `vuln`, `wazuh`, `winrm`, `wiz`, `wufb`, `zscaler`

---

## Classification Criteria

### Implementation Status

**`real`** — the live branch (when `creds` present) makes actual network/API calls:
- boto3, httpx, ldap3, kubernetes.client, paramiko, requests, or similar library calls
- The function does meaningful external I/O with the connector's credentials

**`placeholder`** — credential-gated mock exists (`if not creds: return {"mock": True}`), but the live branch also returns hardcoded data with no real API calls:
- `else: return {"success": True}` or `else: return {"created": True, ...}` with no actual API call
- Only calls `logger.info(...)` and returns hardcoded success

**`partial`** — live branch has some real logic but visibly incomplete:
- Contains `# TODO` or `# stub` in the live path
- Makes a real call but ignores the response meaningfully
- Rollback is a stub while execute is real (flag this specifically)

### Smoke Coverage

**`covered`** — at least one executor in the domain is exercised against live infrastructure in a passing smoke phase in `test_feature_smoke_live.py`

**`partial`** — phase exists but only hits mock path (no real credentials loaded), or only a subset of executors is covered

**`none`** — no smoke phase exercises any executor in this domain

---

## Task 1: Classify All Executors

**Files:**
- Read: `backend/app/connectors/executors/<domain>/*.py` (all 75 domains)
- Create (draft): working notes (in memory, not committed)

This task reads every executor file and applies the classification criteria. The output is a structured list used in Task 2.

- [ ] **Step 1: Read executor files for domains `active_directory` through `helm` (first half)**

For each non-`__init__.py`, non-`_client.py` file in each domain directory, read it and determine:
- Does `execute()` make a real API/network call in the live branch (when creds present)?
- Is it credential-gated mock only?
- Is it a partial implementation?

Domains in this batch: `active_directory`, `ansible`, `ansible_local`, `aws`, `azure`, `azure_ad`, `bicep`, `bind_dns`, `checkov`, `chef_inspec`, `cloudflare`, `cloudformation`, `crowdstrike`, `datadog`, `defender_endpoint`, `elastic`, `entra_id`, `falco`, `freeipa`, `gcp`, `gitea`, `github`, `gitlab`, `google_workspace`, `hashicorp_vault`, `helm`

Key API call patterns to look for:
- **boto3:** `boto3.client(`, `boto3.Session(`
- **httpx:** `httpx.AsyncClient(`, `await client.get(`, `await client.post(`
- **ldap3:** `ldap3.Connection(`
- **kubernetes:** `kubernetes.client.`, `config.load_`
- **paramiko/SSH:** `get_ssh_client(`, `client.exec_command(`
- **requests:** `requests.get(`, `requests.post(`
- **google-api:** `googleapiclient.discovery.build(`
- **hvac/vault:** `hvac.Client(`, `VaultClient`

Record for each file: `domain/filename.py | real|placeholder|partial | brief note`

- [ ] **Step 2: Read executor files for domains `identity` through `zscaler` (second half)**

Domains in this batch: `identity`, `infisical`, `intune`, `jfrog`, `jira`, `keycloak`, `kubernetes`, `laps`, `ldap`, `mongodb`, `nessus`, `nexplane_agent`, `oci`, `offboard_user`, `okta`, `onboard_user`, `openvas`, `opnsense`, `pagerduty`, `paloalto`, `postgres`, `pulumi`, `qualys`, `redis`, `runzero`, `saltstack`, `santa_sync_server`, `sccm`, `sentinelone`, `servicenow`, `slack`, `smtp`, `snyk`, `splunk`, `ssh`, `step_ca`, `tailscale`, `teleport`, `tenable`, `terraform`, `terraform_local`, `vault`, `vuln`, `wazuh`, `winrm`, `wiz`, `wufb`, `zscaler`

Same classification criteria as Step 1.

- [ ] **Step 3: Produce the raw classification list**

Organize all findings into a list grouped by domain. Flag domains where ALL executors are `placeholder` (highest risk tier) vs. domains where most are `real`.

---

## Task 2: Map Smoke Coverage and Write Report

**Files:**
- Read: `backend/tests/smoke/test_feature_smoke_live.py`
- Create: `docs/superpowers/specs/2026-05-26-executor-audit-report.md`

- [ ] **Step 1: Read the smoke test file**

Read `backend/tests/smoke/test_feature_smoke_live.py` in full. For each smoke phase function, note:
- Phase name (e.g., `VULN_MITIGATION`, `CREDENTIAL_EXPIRY`, `SSH_ADVANCED`, etc.)
- Which connector domains are exercised (look for imports of executor modules, connector types, or direct API calls to specific connector endpoints)
- Whether real credentials are used (look for connector lookups from DB vs. hardcoded/mock values)

- [ ] **Step 2: Build the smoke coverage map**

For each of the 75 domains, assign:
- `covered` — a smoke phase exercises real executors in this domain with live credentials
- `partial` — phase exists but mock path only, or coverage is incomplete
- `none` — no smoke phase touches this domain

Known coverage from project history:
- `aws` → covered (VULN_MITIGATION: IAM revoke; CREDENTIAL_EXPIRY: IAM age check)
- `hashicorp_vault` → covered (CREDENTIAL_EXPIRY: vault lease check)
- `ssh` → covered (CREDENTIAL_EXPIRY: authorized_keys audit smoke)
- `ssh` also → covered (SSH_ADVANCED phase if present)

All other domains: determine from reading the file.

- [ ] **Step 3: Write Section 1 — Implementation Status**

Write the table grouped by domain. Within each domain, list `placeholder` and `partial` rows first, then `real`. Use this format:

```markdown
## Section 1: Implementation Status

### active_directory
| Executor | Status | Notes |
|----------|--------|-------|
| discover_groups.py | real | Uses ldap3.Connection to query AD |
| unlock_account.py | real | Uses ldap3 MODIFY_REPLACE on lockoutTime |

### aws
| Executor | Status | Notes |
|----------|--------|-------|
| attach_iam_policy.py | real | boto3 attach_role_policy |
...
```

Only include `placeholder` and `partial` rows with detailed notes. For `real` rows, a brief one-phrase note is sufficient.

- [ ] **Step 4: Write Section 2 — Smoke Coverage**

```markdown
## Section 2: Smoke Coverage

| Domain | Coverage | Phases | Notes |
|--------|----------|--------|-------|
| aws | covered | VULN_MITIGATION, CREDENTIAL_EXPIRY | IAM revoke + age check |
| hashicorp_vault | covered | CREDENTIAL_EXPIRY | Lease check |
| ssh | covered | CREDENTIAL_EXPIRY, SSH_ADVANCED | authorized_keys + install |
| kubernetes | none | — | No smoke phase exists |
...
```

List all 75 domains. Sort: `covered` first, then `partial`, then `none`.

- [ ] **Step 5: Write Section 3 — Backlog Priorities**

Derive three tiers from the tables:

```markdown
## Section 3: Backlog Priorities

### Tier 1 — Placeholder executors in domains with no smoke coverage
(Highest risk: code exists, was never tested live, may silently be wrong)

| Domain | Placeholder Executors | Recommended Action |
|--------|----------------------|-------------------|
| github | archive_repo.py, enable_branch_protection.py, ... | Provision GitHub connector + write smoke phase |
...

### Tier 2 — Real executors in domains with no smoke coverage
(Lower risk: implementation looks correct, unverified against real infra)

| Domain | Executor Count | Recommended Action |
|--------|---------------|-------------------|
| kubernetes | 29 | Provision k8s connector + write smoke phase |
...

### Tier 3 — Partial executors (any domain)
(Known incomplete — finish before smoke)

| Executor | Issue | Recommended Action |
|----------|-------|-------------------|
...
```

- [ ] **Step 6: Write the report header and assemble**

Full file at `docs/superpowers/specs/2026-05-26-executor-audit-report.md`:

```markdown
# Executor Audit Report — 2026-05-26

**Generated by:** Static analysis of `backend/app/connectors/executors/`
**Domains surveyed:** 75
**Executors surveyed:** [total count]
**Date:** 2026-05-26

## Summary

- **Real:** [N] executors across [N] domains
- **Placeholder:** [N] executors across [N] domains
- **Partial:** [N] executors across [N] domains
- **Smoke-covered domains:** [N] / 75
- **Tier 1 domains (placeholder + no smoke):** [N]
- **Tier 2 domains (real + no smoke):** [N]

[Section 1]
[Section 2]
[Section 3]
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-05-26-executor-audit-report.md
git commit -m "docs: executor audit report — implementation status and smoke coverage for 75 domains"
```

---

## Self-Review

**Spec coverage:**
- Classification of executors (real/placeholder/partial): Tasks 1+2 ✅
- Smoke coverage map per domain: Task 2 ✅
- Backlog tiers derived from both tables: Task 2 Step 5 ✅
- Report committed: Task 2 Step 7 ✅

**No placeholders:** All steps specify exact criteria, exact file paths, exact output format. ✅

**Type consistency:** Classification labels (real/placeholder/partial, covered/partial/none) used consistently throughout. ✅
