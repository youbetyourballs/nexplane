# AWS Live Test Infrastructure — Phase 1 & 2 Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stand up real AWS test infrastructure through Nexplane change requests, deploy the nexplane agent, and validate the full EC2 + agent workflow end-to-end. Produces a reusable smoke test suite that runs against live AWS to catch regressions in EC2, SSM, key pair, and agent-related components.

**Architecture:** Two sequential phases. Phase 1 closes code gaps that block live testing (key pair support, IAM profile on launch). Phase 2 runs the test sequence as change requests and codifies it as an executable smoke test script.

**Tech Stack:** FastAPI backend, boto3, AWS EC2/SSM/IAM, nexplane-agent (Go), Python smoke test script.

**AWS Prerequisites (already completed):**
- IAM role `NexplaneEC2TestRole` with `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`
- IAM instance profile `NexplaneEC2TestProfile` containing that role
- `iam:PassRole` inline policy on `nexplane_service_account` scoped to `NexplaneEC2TestRole`

---

## Phase 1: Code Gaps

### 1a. Key Pair Asset Type + Executor

**Problem:** `ec2_launch` has no `KeyName` parameter. Launched instances cannot be SSH'd into.

**Solution:** Add a `create_key_pair` action to the AWS catalog and a new `key_pair` asset type. The executor calls `ec2.create_key_pair()`, stores the private key material in the asset metadata (encrypted at rest via existing asset_metadata JSON column), and returns an `_auto_asset` so the key pair appears in inventory.

**New executor:** `backend/app/connectors/executors/aws/create_key_pair.py`
- Real mode: `ec2.create_key_pair(KeyName=name)` → stores `key_name`, `key_fingerprint`, `private_key_material` in asset_metadata
- Mock mode: returns a fake RSA key fingerprint, no real AWS call
- Rollback: `ec2.delete_key_pair(KeyName=name)`

**New catalog entry** in `aws.json`:
```json
{
  "action_id": "create_key_pair",
  "generic_action": "create_key_pair",
  "action_type": "change",
  "display_name": "Create EC2 Key Pair",
  "applicable_asset_types": ["cloud_account"],
  "executor": "aws.create_key_pair",
  "rollback_action": "delete_key_pair"
}
```

**New change type definition:** `key_pair_create.json`
- Steps: `create_key_pair`
- Single-step, no preflight needed

**New asset type:** Add `key_pair` to `AssetType` enum (backend + frontend). Key pair assets store: `key_name`, `key_fingerprint`, `region`. Private key material stored in metadata but never displayed in UI.

**Frontend:** Add `key_pair` to `ASSET_TYPE_ICONS` (🔐), `ASSET_TYPE_LABELS`, and `CHANGE_TYPE_ASSET_FILTER`. Quick actions on key_pair assets: `rotate_ssh_keys`.

### 1b. IAM Instance Profile + Key Name on EC2 Launch

**Problem:** `resolve_launch_config` doesn't pass `IamInstanceProfile` or `KeyName` to `RunInstances`, so launched instances have no SSM access and no SSH.

**Solution:** Add two optional parameters to `resolve_launch_config` and `launch_instance`:
- `iam_instance_profile` — ARN or name of the instance profile to attach
- `key_name` — name of an existing EC2 key pair

**Changes to `resolve_launch_config.py`:**
- Accept `iam_instance_profile` and `key_name` from parameters
- Pass both through in the returned config dict alongside `ami_id`, `subnet_id`, etc.
- Quick mode default: `iam_instance_profile = "NexplaneEC2TestProfile"` when no creds (mock), empty when real (user must specify)

**Changes to `launch_instance.py`:**
- Read `iam_instance_profile` and `key_name` from parameters (carried from resolve step)
- Include in `run_instances()` call:
  ```python
  kwargs = {
      "ImageId": ami_id, "InstanceType": instance_type,
      "SubnetId": subnet_id, "SecurityGroupIds": security_group_ids,
      "MinCount": 1, "MaxCount": 1, ...
  }
  if iam_instance_profile:
      kwargs["IamInstanceProfile"] = {"Name": iam_instance_profile}
  if key_name:
      kwargs["KeyName"] = key_name
  ```

**Changes to `ec2_launch` outcome template** in `CreateChangeRequest.tsx`:
```json
{
  "mode": "quick",
  "name": "my-new-instance",
  "os": "amazon_linux",
  "iam_instance_profile": "NexplaneEC2TestProfile",
  "key_name": "",
  "rollback_strategy": "terminate_instance"
}
```

**Changes to `planning_engine.py`** — add `iam_instance_profile` and `key_name` to the `resolve_launch_config` and `launch_instance` parameter resolvers.

### 1c. SSM Command Change Type

**Problem:** `run_ssm_command` executor exists and is in the catalog, but there is no `ssm_command` change type definition wiring it into the CR workflow.

**Solution:** Add `backend/app/connectors/change_type_definitions/ssm_command.json`:
```json
{
  "change_type": "ssm_command",
  "display_name": "Run SSM Command",
  "steps": [
    {"generic_action": "run_ssm_command", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

Add `ssm_command` to the backend `ChangeType` enum and frontend `CHANGE_TYPE_META` with outcome template:
```json
{
  "instance_id": "i-0123456789abcdef0",
  "command": "whoami",
  "rollback_strategy": "rollback_unavailable"
}
```

Add to `CHANGE_TYPE_GROUPS` under "EC2" group. Add `ssm_command` → `server` in `CHANGE_TYPE_ASSET_FILTER`.

---

## Phase 2: Live Test Sequence

### Test Sequence (run as Nexplane Change Requests)

Each step is a real CR submitted through the UI or API. Success criteria are listed for each.

**Step 1 — Create key pair**
- Change type: `key_pair_create`
- Target: AWS cloud_account asset
- Desired outcome: `{"key_name": "nexplane-test-key"}`
- Success: key pair asset appears in inventory with fingerprint

**Step 2 — Launch EC2 with SSM + key pair**
- Change type: `ec2_launch`
- Target: AWS cloud_account asset
- Desired outcome: `{"mode": "quick", "name": "nexplane-test-01", "os": "amazon_linux", "iam_instance_profile": "NexplaneEC2TestProfile", "key_name": "nexplane-test-key"}`
- Success: instance reaches `running`, appears in inventory with `instance_id` + `public_ip` metadata

**Step 3 — Verify SSM connectivity**
- Change type: `ssm_command`
- Target: `nexplane-test-01` server asset
- Desired outcome: `{"instance_id": "<from asset>", "command": "whoami && hostname"}`
- Success: execution result shows `ssm-user` and hostname in stdout

**Step 4 — Deploy nexplane agent**
- Change type: `telemetry_agent_deploy`
- Target: `nexplane-test-01`
- Desired outcome: `{"agent_type": "nexplane", "agent_version": "latest"}`
- Success: agent registers in Nexplane and appears as connected

**Step 5 — Run audit via agent**
- Change type: `patch_packages` with `dry_run: true`
- Target: `nexplane-test-01`
- Success: returns list of available patches without applying

**Step 6 — SSH hardening via agent**
- Change type: `enforce_cis_benchmark` with `dry_run: true`
- Target: `nexplane-test-01`
- Success: returns per-control audit results

**Step 7 — Stop instance**
- Change type: `ec2_stop`
- Target: `nexplane-test-01`
- Success: instance reaches `stopped`, asset metadata state updated to `stopped`

**Step 8 — Start instance**
- Change type: `ec2_start`
- Target: `nexplane-test-01`
- Success: instance reaches `running`, agent reconnects

**Step 9 — Terminate instance (rollback test)**
- Change type: `ec2_terminate`
- Target: `nexplane-test-01`
- Success: instance terminates, asset removed from inventory

**Step 10 — Delete key pair**
- Change type: `key_pair_create` rollback (or manual CR)
- Success: key pair removed from AWS and inventory

### Smoke Test Script

**File:** `backend/tests/smoke/test_aws_live.py`

A Python script (not pytest — runs against live AWS and takes minutes) that:
1. Uses the Nexplane REST API (not internal DB) to submit real CRs
2. Polls CR status until `completed` or `failed` (with 5-minute timeout per step)
3. Asserts expected outcomes at each step
4. Cleans up all created resources even on failure (key pair, EC2 instance)
5. Produces a structured pass/fail report

**Trigger:** Run manually with `python backend/tests/smoke/test_aws_live.py --base-url http://localhost:8000 --token <api-token>`. Intended to be run before merging any PR that touches EC2 executors, agent deploy, SSM, key pair, or the workflow engine.

**Scope guard:** The script checks that it's running against a non-production account by verifying the target instance names start with `nexplane-test-` before executing any destructive actions.

**Cleanup guarantee:** Uses `try/finally` around the full sequence. On any failure, attempts to terminate any running `nexplane-test-*` instances and delete any `nexplane-test-*` key pairs.

---

## Gaps Discovered During Design (to fix during implementation)

1. `ssm_command` change type missing from enum + definitions — **fix in Phase 1c**
2. `key_pair_create` change type missing entirely — **fix in Phase 1a**
3. `key_pair` asset type missing — **fix in Phase 1a**
4. `telemetry_agent_deploy` currently uses SSH connector steps, not SSM — **investigate and fix if needed**
5. `patch_packages` nexplane_agent steps need agent running — verify agent registration flow works end-to-end
6. `run_ssm_command` executor takes `command` as a free-form string but the safety engine blocks freeform commands — **need to bypass for SSM (it uses AWS-managed trust, not shell injection)**
7. `ec2_terminate` outcome template missing `instance_id` pre-fill from asset metadata — **fix in useEffect in CreateChangeRequest**

---

## What This Does Not Cover (Later Phases)

- Terraform local executor (Phase 4)
- Ansible standalone executor (Phase 5)
- RDS, ELB, S3 live testing (Phase 6)
- Multi-region testing
- Okta / Active Directory live testing (requires separate paid accounts)
