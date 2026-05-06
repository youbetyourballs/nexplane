# Smoke Test CR Title Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize all CR titles in the smoke test files to use `[Phase X] description` format instead of the inconsistent `Smoke:`, `Smoke-X:`, and `Agent-smoke:` prefixes.

**Architecture:** Pure search-and-replace across 4 smoke test files. No logic changes. Each file is updated in one task and verified with a syntax check.

**Tech Stack:** Python 3.12, existing smoke test infrastructure

---

## Files

**Modify:**
- `backend/tests/smoke/test_aws_live.py`
- `backend/tests/smoke/test_gcp_live.py`
- `backend/tests/smoke/test_azure_live.py`
- `backend/tests/smoke/test_agent_live.py`

---

### Task 1: Standardize titles in `test_aws_live.py`

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Apply ALL of these replacements (exact string match, replace first argument of each `run_cr`/`_run_cr_with_timeout` call):

- [ ] **Step 1: Apply title replacements**

Replace each title string as follows:

```python
# Phase A
"Smoke: create key pair"      → "[Phase A] create key pair"
"Smoke: launch EC2"           → "[Phase A] launch EC2 instance"
"Smoke: SSM whoami"           → "[Phase A] SSM whoami"
"Smoke: tailscale join"       → "[Phase A] tailscale join"
"Smoke: deploy agent"         → "[Phase A] deploy nexplane agent"

# Phase B
"Smoke: patch audit via SSM"  → "[Phase B] patch audit via SSM"
"Smoke: collect system info"  → "[Phase B] collect system info"
"Smoke: install CloudWatch agent" → "[Phase B] install CloudWatch agent"

# Phase C
"Smoke: terraform apply S3 bucket" → "[Phase C] terraform apply S3 bucket"

# Phase D
"Smoke: ansible local test"   → "[Phase D] ansible local playbook"
"Smoke: install htop via SSM" → "[Phase D] install htop via SSM"
"Smoke: remove htop via SSM"  → "[Phase D] remove htop via SSM"

# Phase E
"Smoke-E: stop instance"            → "[Phase E] stop EC2 instance"
"Smoke-E: start instance"           → "[Phase E] start EC2 instance"
"Smoke-E: reboot instance"          → "[Phase E] reboot EC2 instance"
"Smoke-E: SSM verify post-reboot"   → "[Phase E] SSM verify post-reboot"
"Smoke-E: create EBS snapshot"      → "[Phase E] create EBS snapshot"
"Smoke-E: verify post-snapshot"     → "[Phase E] SSM verify post-snapshot"

# Phase F
"Smoke-F: add inbound rule"    → "[Phase F] add security group inbound rule"
"Smoke-F: remove inbound rule" → "[Phase F] remove security group inbound rule"

# Phase G
"Smoke-G: create IAM user" → "[Phase G] create IAM user"

# Phase H
"Smoke-H: create S3 bucket"      → "[Phase H] create S3 bucket"
"Smoke-H: configure lifecycle"   → "[Phase H] configure S3 lifecycle"

# Phase I
"Smoke-I: create hosted zone" → "[Phase I] create Route53 hosted zone"
"Smoke-I: create A record"    → "[Phase I] create Route53 A record"
"Smoke-I: update A record"    → "[Phase I] update Route53 A record"
"Smoke-I: delete A record"    → "[Phase I] delete Route53 A record"

# Phase J
"Smoke-J: create RDS instance"  → "[Phase J] create RDS instance"
"Smoke-J: create RDS snapshot"  → "[Phase J] create RDS snapshot"

# Phase K
"Smoke-K: create CPU alarm"          → "[Phase K] create CloudWatch CPU alarm"
"Smoke-K: create custom metric alarm"→ "[Phase K] create CloudWatch custom metric alarm"
"Smoke-K: push metric data via SSM"  → "[Phase K] push metric data via SSM"

# Phase P
"Smoke-P: create IAM user"    → "[Phase P] create IAM user"
"Smoke-P: attach IAM policy"  → "[Phase P] attach IAM policy"
"Smoke-P: rotate IAM key"     → "[Phase P] rotate IAM access key"
"Smoke-P: disable IAM user"   → "[Phase P] disable IAM user"
"Smoke-P: enable IAM user"    → "[Phase P] enable IAM user"

# Phase Q
"Smoke-Q: create S3 bucket"  → "[Phase Q] create S3 bucket"
"Smoke-Q: put bucket policy" → "[Phase Q] put S3 bucket policy"
"Smoke-Q: tag S3 bucket"     → "[Phase Q] tag S3 bucket"

# Phase R
"Smoke-R: create Route53 zone"     → "[Phase R] create Route53 zone"
"Smoke-R: dr_dns_failover_route53" → "[Phase R] DR DNS failover Route53"

# Phase S
"Smoke-S: create RDS instance"     → "[Phase S] create RDS instance"
"Smoke-S: create RDS snapshot"     → "[Phase S] create RDS snapshot"
"Smoke-S: verify RDS backup"       → "[Phase S] verify RDS backup"
"Smoke-S: create RDS read replica" → "[Phase S] create RDS read replica"
"Smoke-S: promote RDS replica"     → "[Phase S] promote RDS replica"
"Smoke-S: delete promoted instance"→ "[Phase S] delete promoted RDS instance"

# Phase T
"Smoke-T: tag EC2 instance"      → "[Phase T] tag EC2 instance"
"Smoke-T: remove nexplane agent" → "[Phase T] remove nexplane agent"
"Smoke-T: redeploy nexplane agent"→ "[Phase T] redeploy nexplane agent"
```

Use the Edit tool for each replacement. For any `f"Smoke-K: ..."` or similar format strings, update accordingly.

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "refactor(smoke): standardize CR titles to [Phase X] format in test_aws_live.py"
```

---

### Task 2: Standardize titles in `test_gcp_live.py`

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

- [ ] **Step 1: Apply title replacements**

```python
# Phase L
"Smoke-L: launch GCE instance" → "[Phase L] launch GCE instance"

# Phase M
"Smoke-M: stop GCE instance"    → "[Phase M] stop GCE instance"
"Smoke-M: start GCE instance"   → "[Phase M] start GCE instance"
"Smoke-M: reboot GCE instance"  → "[Phase M] reboot GCE instance"
"Smoke-M: create disk snapshot" → "[Phase M] create GCE disk snapshot"

# Phase N
"Smoke-N: create GCP firewall rule" → "[Phase N] create GCP firewall rule"

# Phase O
"Smoke-O: block public GCS bucket access" → "[Phase O] block public GCS bucket access"

# Phase P
"Smoke-P: rotate service account key" → "[Phase P] rotate GCP service account key"
"Smoke-P: disable service account"    → "[Phase P] disable GCP service account"

# Phase Q
"Smoke-Q: terraform apply GCS bucket (GCP)" → "[Phase Q] terraform apply GCS bucket"

# Phase R
"Smoke-R: ansible local playbook (GCP)" → "[Phase R] ansible local playbook"
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_gcp_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "refactor(smoke): standardize CR titles to [Phase X] format in test_gcp_live.py"
```

---

### Task 3: Standardize titles in `test_azure_live.py`

**Files:**
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Apply title replacements**

```python
# Phase N
"Smoke-N: launch Azure VM" → "[Phase N] launch Azure VM"

# Phase O
"Smoke-O: stop Azure VM"         → "[Phase O] stop Azure VM"
"Smoke-O: start Azure VM"        → "[Phase O] start Azure VM"
"Smoke-O: reboot Azure VM"       → "[Phase O] reboot Azure VM"
"Smoke-O: create disk snapshot"  → "[Phase O] create Azure disk snapshot"

# Phase P
"Smoke-P: update NSG rule" → "[Phase P] update Azure NSG rule"

# Phase Q
"Smoke-Q: disable public blob access" → "[Phase Q] disable Azure public blob access"
"Smoke-Q: rotate storage key"         → "[Phase Q] rotate Azure storage key"

# Phase R
"Smoke-R: tag Azure VM" → "[Phase R] tag Azure VM"

# Phase S
"Smoke-S: terraform apply Azure resource group" → "[Phase S] terraform apply Azure resource group"

# Phase T
"Smoke-T: ansible local playbook (Azure)" → "[Phase T] ansible local playbook"
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_azure_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_azure_live.py
git commit -m "refactor(smoke): standardize CR titles to [Phase X] format in test_azure_live.py"
```

---

### Task 4: Standardize titles in `test_agent_live.py`

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Apply title replacements**

The `_ssm` helper takes `label` as the second arg and builds the CR title as `f"Agent-smoke: {label}"`. Change this to `f"[Phase {phase_name}] {label}"` by updating the helper signature and callers.

Replace the `_ssm` function:
```python
# Old:
def _ssm(client, instance_asset_id, instance_id, label, command):
    client.run_cr(
        f"Agent-smoke: {label}", "ssm_command", ...
    )

# New:
def _ssm(client, instance_asset_id, instance_id, phase, label, command):
    client.run_cr(
        f"[Phase {phase}] {label}", "ssm_command", ...
    )
```

Update every `_ssm(...)` call to pass the phase group name as the new second argument:
- Calls in `run_linux_patch_aws` → phase=`"linux_patch-aws-linux"`
- Calls in `run_ossecurity_aws` → phase=`"ossecurity-aws-linux"`
- Calls in `run_linuxauth_aws` → phase=`"linuxauth-aws-linux"`
- Calls in `run_crossplatform_aws` → phase=`"crossplatform-aws-linux"`
- Calls in `run_compliance_aws` → phase=`"compliance-aws-linux"`
- Calls in `run_forensics_aws` → phase=`"forensics-aws-linux"`
- Calls in `run_fleet_aws` → phase=`"fleet-aws-linux"`
- Calls in `run_backup_aws` → phase=`"backup-aws-linux"`
- Calls in `run_reboot_aws` → phase=`"reboot-aws-linux"`
- Calls in `run_credrotation_aws` → phase=`"credrotation-aws-linux"`
- Calls in `run_iac_aws` → phase=`"iac-aws-linux"`
- Calls in `run_linuxupgrade_aws` → phase=`"linuxupgrade-aws-linux"`

For the Windows `_psm` helper:
```python
# Old:
def _psm(label, command, instance_asset_id, instance_id):
    client.run_cr(f"Agent-smoke-win: {label}", ...)

# New (closure already captures phase info, just update the title format):
def _psm(label, command, instance_asset_id, instance_id):
    client.run_cr(f"[Phase win_patch-aws-win] {label}", ...)  # or winharden-aws-win based on context
```

Since `_psm` is a nested function inside `run_aws_windows_track`, split it to check which phase group is active or just use `[Phase aws-windows]` for all Windows SSM calls.

For setup CRs in `_setup_aws_linux_instance`:
```python
"Agent-smoke: create key pair"  → "[Phase setup-aws-linux] create key pair"
"Agent-smoke: launch EC2"       → "[Phase setup-aws-linux] launch EC2 instance"
"Agent-smoke: SSM whoami"       → "[Phase setup-aws-linux] SSM whoami"
"Agent-smoke: tailscale join"   → "[Phase setup-aws-linux] tailscale join"
"Agent-smoke: deploy agent"     → "[Phase setup-aws-linux] deploy nexplane agent"
```

For setup CRs in `run_aws_windows_track`:
```python
"Agent-smoke-win: create key pair"     → "[Phase setup-aws-win] create key pair"
"Agent-smoke-win: launch Windows EC2" → "[Phase setup-aws-win] launch Windows EC2 instance"
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_agent_live.py').read()); print('syntax OK')"
```

- [ ] **Step 3: Verify stubs still run (no credentials needed)**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --cloud gcp --os all --phases linux_patch --gcp-project nexplane 2>&1 | tail -5
```

Expected: `✅ ALL SELECTED TRACKS PASSED`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "refactor(smoke): standardize CR titles to [Phase X] format in test_agent_live.py"
```
