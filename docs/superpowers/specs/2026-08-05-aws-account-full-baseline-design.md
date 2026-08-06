# AWS Account Full Baseline Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an `aws_account_full_baseline` CR type that runs all three existing AWS baseline sub-executors in sequence as a single idempotent CR: hardening (IAM password policy, S3 block public access, VPC flow logs) → monitoring (CloudTrail, GuardDuty, SecurityHub, AWS Config across all regions) → IAM role baseline (ReadOnly, SecurityAudit, BreakGlass roles). This is the single CR an operator runs on a new AWS account to bring it to Nexplane's security baseline.

**Architecture:** Thin orchestration executor that calls the three existing sub-executors in order. Each sub-executor already has full rollback. The combined CR rolls back in FILO order: roles → monitoring → hardening. No new agent commands needed — pure AWS API calls. The orchestration executor imports and calls the sub-executors' `execute()` and `rollback()` functions directly rather than dispatching separate CRs.

**Tech Stack:** Python asyncio, boto3, existing sub-executors `aws_account_baseline_hardening`, `aws_account_baseline_monitoring`, `aws_iam_role_baseline`.

## Global Constraints

- Executor lives in `backend/app/connectors/executors/aws/aws_account_full_baseline.py`
- Import sub-executors directly: `from app.connectors.executors.aws import aws_account_baseline_hardening, aws_account_baseline_monitoring, aws_iam_role_baseline`
- `desired_outcome` is the only parameter channel
- Each sub-step's result is stored under its own key in execution_result
- If any sub-step fails, stop and return partial result; rollback reverses only the completed steps (FILO)
- ROLLBACK_CAPABILITY = `"full"`
- Sub-executors are already smoke-verified — no need to re-smoke their individual behavior, only the orchestration
- Smoke test verifies the combined CR lifecycle against a real AWS account

---

## CR Type: `aws_account_full_baseline`

**Files:**
- Create: `backend/app/connectors/executors/aws/aws_account_full_baseline.py`
- Create: `backend/app/connectors/change_type_definitions/aws_account_full_baseline.json`
- Modify: `backend/app/models/change_request.py` — add `aws_account_full_baseline` after `aws_iam_role_baseline`
- Modify: `backend/app/connectors/catalog/aws.json` — add catalog entry
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_aws_account_full_baseline.py`

**Parameters (all in `desired_outcome`):**
- `role_name_prefix`: string (default `"Nexplane"`) — passed through to `aws_iam_role_baseline`
- `skip_if_enabled`: bool (default `true`) — all sub-executors are already idempotent; this controls whether to skip steps already at target state
- `dry_run`: bool — passed through to all sub-executors

**Executor:**

```python
ROLLBACK_CAPABILITY = "full"

async def execute(parameters, asset_ids, connector):
    from app.connectors.executors.aws import (
        aws_account_baseline_hardening,
        aws_account_baseline_monitoring,
        aws_iam_role_baseline,
    )

    steps_completed = []
    results = {}

    # Step 1: Hardening
    h_result = await aws_account_baseline_hardening.execute(parameters, asset_ids, connector)
    results["hardening"] = h_result
    if h_result.get("status") == "failed":
        return {"status": "failed", "failed_step": "hardening", "steps_completed": steps_completed, **results}
    steps_completed.append("hardening")

    # Step 2: Monitoring
    m_result = await aws_account_baseline_monitoring.execute(parameters, asset_ids, connector)
    results["monitoring"] = m_result
    if m_result.get("status") == "failed":
        return {"status": "failed", "failed_step": "monitoring", "steps_completed": steps_completed, **results}
    steps_completed.append("monitoring")

    # Step 3: IAM role baseline
    r_result = await aws_iam_role_baseline.execute(parameters, asset_ids, connector)
    results["iam_roles"] = r_result
    if r_result.get("status") == "failed":
        return {"status": "failed", "failed_step": "iam_roles", "steps_completed": steps_completed, **results}
    steps_completed.append("iam_roles")

    return {
        "status": "completed",
        "steps_completed": steps_completed,
        "hardening": h_result,
        "monitoring": m_result,
        "iam_roles": r_result,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters, asset_ids, connector, execution_result):
    from app.connectors.executors.aws import (
        aws_account_baseline_hardening,
        aws_account_baseline_monitoring,
        aws_iam_role_baseline,
    )

    steps_completed = execution_result.get("steps_completed", [])
    rollback_results = {}

    # FILO: reverse order of what was completed
    if "iam_roles" in steps_completed:
        rollback_results["iam_roles"] = await aws_iam_role_baseline.rollback(
            parameters, asset_ids, connector, execution_result.get("iam_roles", {})
        )
    if "monitoring" in steps_completed:
        rollback_results["monitoring"] = await aws_account_baseline_monitoring.rollback(
            parameters, asset_ids, connector, execution_result.get("monitoring", {})
        )
    if "hardening" in steps_completed:
        rollback_results["hardening"] = await aws_account_baseline_hardening.rollback(
            parameters, asset_ids, connector, execution_result.get("hardening", {})
        )

    return {"rolled_back": True, "rollback_results": rollback_results}
```

**change_type_definition:**
```json
{
  "change_type": "aws_account_full_baseline",
  "display_name": "AWS Account Full Baseline",
  "steps": [{"generic_action": "aws_account_full_baseline", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "aws_account_full_baseline",
  "rollback_connector_type": "aws"
}
```

**Catalog entry in `aws.json`:**
```json
{
  "action_id": "aws_account_full_baseline",
  "generic_action": "aws_account_full_baseline",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "AWS Account Full Security Baseline",
  "description": "Single idempotent CR that applies all three Nexplane AWS security baselines in sequence: hardening (IAM password policy, S3 block public access, VPC flow logs), monitoring (CloudTrail, GuardDuty, SecurityHub, Config across all regions), and IAM role baseline (ReadOnly, SecurityAudit, BreakGlass). Safe to run on any AWS account — idempotent, skips already-enabled controls.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "role_name_prefix", "type": "string", "required": false, "default": "Nexplane"},
    {"name": "dry_run", "type": "boolean", "required": false, "default": false}
  ],
  "executor": "aws.aws_account_full_baseline",
  "rollback_strategy": "executor",
  "estimated_duration_seconds": 600,
  "blast_radius_hint": "account_policy",
  "safety_notes": [
    "Idempotent — safe to run multiple times",
    "Enabling GuardDuty and SecurityHub incurs AWS charges (negligible for most accounts)",
    "IAM password policy applies to all IAM users in the account"
  ],
  "smoke_verified": false
}
```

**Smoke test:**

Phase 1: Setup
- Get AWS connector creds from DB
- Confirm we can call `sts.get_caller_identity()` — smoke runs against the existing platform AWS account

Phase 2: Execute
- CR lifecycle: `aws_account_full_baseline` with defaults
- Assert `status == "completed"`
- Assert `steps_completed` contains all three steps
- Assert `hardening.status` not "failed"
- Assert `monitoring.status` not "failed" (some services may already be enabled — idempotent)
- Assert `iam_roles.status` not "failed"
- Spot-check: `iam.get_account_password_policy()` returns policy with MinimumPasswordLength >= 14
- Spot-check: `cloudtrail.describe_trails()` has at least one multi-region trail

Phase 3: Rollback
- Trigger rollback on the CR
- Assert CR reaches `rolled_back` status
- Spot-check: `iam_role_baseline` rollback deletes the Nexplane-prefixed roles

Phase 4: (no teardown needed — idempotent, rollback cleaned up)

---

## Backlog additions (cross-cloud parity — account baseline hardening)

- **GCP account full baseline** — combine `gcp_account_baseline_monitoring` + new `gcp_account_baseline_hardening` (Organization Policy: disable public IPs, enforce uniform bucket-level access, require OS Login, restrict resource locations) + `gcp_iam_role_baseline` (create custom viewer/auditor roles)
- **Azure account full baseline** — combine `azure_account_baseline_monitoring` + new `azure_account_baseline_hardening` (Azure Policy: require tags, deny public storage, require HTTPS, enforce MFA via Conditional Access) + `azure_iam_role_baseline` (create custom reader/auditor assignments)
- **OCI account full baseline** — combine `oci_account_baseline_monitoring` + new `oci_account_baseline_hardening` (Security Zones, Cloud Guard targets, IAM password policy) + `oci_iam_role_baseline`
