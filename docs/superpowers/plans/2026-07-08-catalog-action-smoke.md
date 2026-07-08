# Catalog Action Smoke — Wire Core Phases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the 6 existing `CATALOG_ACTION` phases in `test_catalog_action_live.py` into `run_on_ec2.py` routing so they run on EC2 runners, and run the smoke to verify they pass.

**Architecture:** One-line routing change in `run_on_ec2.py` adding a `CATALOG_ACTION_PHASES` set and an `elif` branch pointing at `test_catalog_action_live.py`. All 6 phase implementations already exist. Commercial wizard work is tracked in `nexplane-deploy/docs/BACKLOG.md §6` and is a separate session.

**Tech Stack:** Python, existing smoke runner pattern

## Global Constraints

- All smoke assertions run against live platform at `http://172.31.1.233:8000`
- Smoke runs from EC2 runner, not laptop
- Auto-merge to master, no PRs

---

## File Map

- Modify: `backend/tests/smoke/run_on_ec2.py:967-983`

---

### Task 1: Wire CATALOG_ACTION phases + run smoke

**Files:**
- Modify: `backend/tests/smoke/run_on_ec2.py`

**Interfaces:**
- Consumes: `test_catalog_action_live.py` (already exists, all 6 phases implemented)
- Produces: phases `CATALOG_DISCOVERY`, `CATALOG_CR_LIFECYCLE`, `CATALOG_ACTION_ERRORS`, `CATALOG_ROLLBACK`, `CATALOG_WORKFLOW`, `CATALOG_WORKFLOW_PARTIAL_FAILURE` route to `test_catalog_action_live.py` on the runner

- [ ] **Step 1: Add CATALOG_ACTION_PHASES set and routing branch**

In `run_on_ec2.py`, find the block at ~line 967 where `PLATFORM_PHASES` and `BACKUP_SCHEDULER_PHASES` are defined:

```python
        PLATFORM_PHASES = {
            "IR_ISOLATE_HOST", "IR_PRESERVE_EVIDENCE", "IR_LOCKDOWN_ACCOUNT", "IR_PHISHING_RESPONSE",
            "RUNBOOK_ONBOARDING", "RUNBOOK_ACCOUNT_COMPROMISE", "RUNBOOK_PATCH_CAMPAIGN",
            "ACCESS_REVIEW", "PROJECT_MICROSEG", "VULN_PIPELINE",
        }
        BACKUP_SCHEDULER_PHASES = {
            "BACKUP_SCHEDULER", "PLATFORM_UPGRADE_ROLLBACK", "AD_MEMBER_TIERS",
            "LOCAL_FILES_BACKUP", "DATABASE_DUMP_BACKUP", "STORAGE_SYNC",
            "LVM_SNAPSHOT", "NFS_FILES", "MANAGED_DB_SNAPSHOT", "DISK2VHD", "MGN_REPLICATION",
        }
        selected_phases = set(args.phases.split(","))
        if selected_phases & PLATFORM_PHASES:
            test_script = "test_platform_live.py"
        elif selected_phases & BACKUP_SCHEDULER_PHASES:
            test_script = "test_backup_scheduler_live.py"
        else:
            test_script = "test_aws_live.py"
```

Replace with:

```python
        PLATFORM_PHASES = {
            "IR_ISOLATE_HOST", "IR_PRESERVE_EVIDENCE", "IR_LOCKDOWN_ACCOUNT", "IR_PHISHING_RESPONSE",
            "RUNBOOK_ONBOARDING", "RUNBOOK_ACCOUNT_COMPROMISE", "RUNBOOK_PATCH_CAMPAIGN",
            "ACCESS_REVIEW", "PROJECT_MICROSEG", "VULN_PIPELINE",
        }
        BACKUP_SCHEDULER_PHASES = {
            "BACKUP_SCHEDULER", "PLATFORM_UPGRADE_ROLLBACK", "AD_MEMBER_TIERS",
            "LOCAL_FILES_BACKUP", "DATABASE_DUMP_BACKUP", "STORAGE_SYNC",
            "LVM_SNAPSHOT", "NFS_FILES", "MANAGED_DB_SNAPSHOT", "DISK2VHD", "MGN_REPLICATION",
        }
        CATALOG_ACTION_PHASES = {
            "CATALOG_DISCOVERY", "CATALOG_CR_LIFECYCLE", "CATALOG_ACTION_ERRORS",
            "CATALOG_ROLLBACK", "CATALOG_WORKFLOW", "CATALOG_WORKFLOW_PARTIAL_FAILURE",
        }
        selected_phases = set(args.phases.split(","))
        if selected_phases & PLATFORM_PHASES:
            test_script = "test_platform_live.py"
        elif selected_phases & BACKUP_SCHEDULER_PHASES:
            test_script = "test_backup_scheduler_live.py"
        elif selected_phases & CATALOG_ACTION_PHASES:
            test_script = "test_catalog_action_live.py"
        else:
            test_script = "test_aws_live.py"
```

- [ ] **Step 2: Verify the change parses**

```bash
python3 -c "import ast; ast.parse(open('backend/tests/smoke/run_on_ec2.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit and push to EC2**

```bash
git add backend/tests/smoke/run_on_ec2.py
git commit -m "feat: wire CATALOG_ACTION phases into run_on_ec2 routing"
git push origin master
```

Then pull on EC2:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git pull"
```

- [ ] **Step 4: Run the 6 core CATALOG_ACTION phases**

```bash
cd backend/tests/smoke
python run_on_ec2.py \
  --base-url http://172.31.1.233:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases CATALOG_DISCOVERY,CATALOG_CR_LIFECYCLE,CATALOG_ACTION_ERRORS,CATALOG_ROLLBACK,CATALOG_WORKFLOW,CATALOG_WORKFLOW_PARTIAL_FAILURE
```

Expected: `ALL CATALOG_ACTION_SMOKE PHASES PASSED`

- [ ] **Step 5: Fix any failing phases and re-run**

If a phase fails, check the streamed log output for the assertion that failed. Fix in `backend/tests/smoke/test_catalog_action_live.py`, push, pull on EC2, re-run only the failing phase:

```bash
python run_on_ec2.py --base-url http://172.31.1.233:8000 --email admin@acme.example --password admin123 --phases <FAILING_PHASE>
```

Commit any fixes:
```bash
git add backend/tests/smoke/test_catalog_action_live.py
git commit -m "fix: catalog action smoke phase adjustments"
git push origin master
```
