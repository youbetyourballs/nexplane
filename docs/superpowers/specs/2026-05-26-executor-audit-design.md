# Executor Audit Design

> **For agentic workers:** This is an analysis task. The deliverable is a report, not production code. Run the static analysis and produce the output document — no migrations, no new models, no UI.

**Goal:** Audit all executor files to classify their implementation status and map them against existing smoke test coverage, producing a backlog-feeding report.

---

## Output

One file: `docs/superpowers/specs/2026-05-26-executor-audit-report.md`

Committed to git. Not a plan — a reference document for future session prioritization.

---

## Analysis Method

**Static analysis only.** No infra required. Read executor files and the smoke test file; classify by code inspection.

### Step 1: Classify each executor

For every `*.py` file under `backend/app/connectors/executors/` (excluding `_client.py`, `__init__.py`):

**`real`** — the live branch (when `creds` is present) makes actual network/API calls:
- `boto3.client(...)`, `httpx.AsyncClient`, `ldap3.Connection`, `kubernetes.client`, `requests.get`, etc.
- The function does meaningful work with the connector's credentials

**`placeholder`** — credential-gated mock exists, but the live branch also returns hardcoded data with no real API calls:
- `if not creds: return {"mock": True}` ... `else: return {"success": True}` (no actual API call in the else)
- Or: only calls `logger.info(...)` and returns success without touching any external system

**`partial`** — live branch has some real logic but is visibly incomplete:
- Has a `# TODO` or `# stub` comment in the live path
- Calls one endpoint but skips rollback implementation
- Makes a real call but ignores the response

### Step 2: Map smoke coverage per connector domain

Read `backend/tests/smoke/test_feature_smoke_live.py`. For each connector domain:

**`covered`** — at least one executor in the domain is exercised against live infrastructure in a passing smoke phase

**`partial`** — some executors covered, others not; or the phase exists but only hits the mock path (no real credentials)

**`none`** — no smoke phase exercises any executor in this domain

Connector domains to report on:
`aws`, `azure`, `gcp`, `active_directory`, `github`, `okta`, `kubernetes`, `hashicorp_vault`, `step_ca`, `ssh`, `oci`, `palo_alto`, `crowdstrike`, `wiz`

---

## Report Format

### Section 1: Implementation Status

```
| Executor | Domain | Status | Notes |
|----------|--------|--------|-------|
| aws/rotate_iam_key.py | aws | real | boto3 delete_access_key + create_access_key |
| kubernetes/delete_pod.py | kubernetes | real | k8s CoreV1Api |
| github/archive_repo.py | github | placeholder | live branch returns {"archived": True} hardcoded |
```

Group by domain. Within each domain, list `placeholder` and `partial` entries first (the actionable ones), then `real`.

### Section 2: Smoke Coverage

```
| Domain | Coverage | Phases | Notes |
|--------|----------|--------|-------|
| aws | covered | VULN_MITIGATION, CREDENTIAL_EXPIRY | IAM key revoke + age check |
| kubernetes | none | — | No smoke phase exists |
| github | none | — | No smoke phase exists |
```

### Section 3: Backlog Priorities

Derived from the two tables. Three tiers:

**Tier 1 — Placeholder executors in domains with no smoke coverage** (highest risk: code exists, was never tested live, may be broken)

**Tier 2 — Real executors in domains with no smoke coverage** (lower risk: logic looks right, but unverified against real infra)

**Tier 3 — Partial executors (any domain)** (known incomplete — need finishing before smoke)

Each tier lists the domain and recommended next action (e.g., "provision GitHub connector + write smoke phase").

---

## Files

| Action | Path |
|--------|------|
| Create | `docs/superpowers/specs/2026-05-26-executor-audit-report.md` |
