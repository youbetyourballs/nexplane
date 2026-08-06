# Implementation Plan: aws_account_full_baseline CR Type

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to execute this plan task-by-task. Checkbox (`- [ ]`) syntax is for tracking.

**Spec:** `docs/superpowers/specs/2026-08-05-aws-account-full-baseline-design.md`
**Date:** 2026-08-05
**Scope:** One new CR type — thin orchestration executor over three existing, smoke-verified sub-executors.

---

## Pre-work notes (read before touching any file)

1. **The import alias differs from the catalog name.** The `iam_role_baseline` sub-executor lives at `backend/app/connectors/executors/aws/iam_role_baseline.py` (NOT `aws_iam_role_baseline.py`). The catalog `executor` field says `aws.iam_role_baseline`. The import must be `from app.connectors.executors.aws import iam_role_baseline`.

2. **Sub-executor rollback signatures are 3-arg** (not 4). All three sub-executors use `rollback(parameters, execution_result, connector)` — no `asset_ids` parameter. The orchestrator's rollback calls must match.

3. **No migrations directory `versions/` — Alembic lives at `backend/alembic/versions/`.** Most recent migration: `20260803_add_container_image_transfer_change_type.py`, down_revision `ecs001`. New migration's `down_revision` must be `20260803`.

4. **`aws_account_baseline_hardening` rollback does not accept `asset_ids`** — confirmed from source (line 298). Same for monitoring (line 383) and iam_role_baseline (line 165).

---

## Task 1 — Orchestration executor

**File to create:** `backend/app/connectors/executors/aws/aws_account_full_baseline.py`

- [ ] Create the file with the content below exactly.

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS Account Full Baseline orchestration executor.

Runs all three AWS baseline sub-executors in sequence as a single idempotent CR:
  1. aws_account_baseline_hardening  — IAM password policy, S3 block public access, VPC flow logs
  2. aws_account_baseline_monitoring — CloudTrail, GuardDuty, SecurityHub, Config (all regions)
  3. iam_role_baseline               — ReadOnly, SecurityAudit, BreakGlass IAM roles

FILO rollback: roles → monitoring → hardening.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.aws import (
        aws_account_baseline_hardening,
        aws_account_baseline_monitoring,
        iam_role_baseline,
    )

    steps_completed = []
    results = {}

    # Step 1: Hardening
    try:
        h_result = await aws_account_baseline_hardening.execute(parameters, asset_ids, connector)
    except Exception as e:
        logger.error("aws_account_full_baseline: hardening step raised: %s", e)
        h_result = {"status": "failed", "error": str(e)}

    results["hardening"] = h_result
    if h_result.get("status") == "failed" or h_result.get("failed"):
        return {
            "status": "failed",
            "failed_step": "hardening",
            "steps_completed": steps_completed,
            **results,
        }
    steps_completed.append("hardening")

    # Step 2: Monitoring
    try:
        m_result = await aws_account_baseline_monitoring.execute(parameters, asset_ids, connector)
    except Exception as e:
        logger.error("aws_account_full_baseline: monitoring step raised: %s", e)
        m_result = {"status": "failed", "error": str(e)}

    results["monitoring"] = m_result
    if m_result.get("status") == "failed" or m_result.get("failed"):
        return {
            "status": "failed",
            "failed_step": "monitoring",
            "steps_completed": steps_completed,
            **results,
        }
    steps_completed.append("monitoring")

    # Step 3: IAM role baseline
    try:
        r_result = await iam_role_baseline.execute(parameters, asset_ids, connector)
    except Exception as e:
        logger.error("aws_account_full_baseline: iam_roles step raised: %s", e)
        r_result = {"status": "failed", "error": str(e)}

    results["iam_roles"] = r_result
    if r_result.get("status") == "failed":
        return {
            "status": "failed",
            "failed_step": "iam_roles",
            "steps_completed": steps_completed,
            **results,
        }
    steps_completed.append("iam_roles")

    return {
        "status": "completed",
        "steps_completed": steps_completed,
        "hardening": h_result,
        "monitoring": m_result,
        "iam_roles": r_result,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, asset_ids: list, connector, execution_result: dict) -> dict:
    from app.connectors.executors.aws import (
        aws_account_baseline_hardening,
        aws_account_baseline_monitoring,
        iam_role_baseline,
    )

    steps_completed = execution_result.get("steps_completed", [])
    rollback_results = {}

    # FILO: reverse order of what was completed
    if "iam_roles" in steps_completed:
        try:
            rollback_results["iam_roles"] = await iam_role_baseline.rollback(
                parameters, execution_result.get("iam_roles", {}), connector
            )
        except Exception as e:
            logger.error("aws_account_full_baseline: iam_roles rollback raised: %s", e)
            rollback_results["iam_roles"] = {"rolled_back": False, "error": str(e)}

    if "monitoring" in steps_completed:
        try:
            rollback_results["monitoring"] = await aws_account_baseline_monitoring.rollback(
                parameters, execution_result.get("monitoring", {}), connector
            )
        except Exception as e:
            logger.error("aws_account_full_baseline: monitoring rollback raised: %s", e)
            rollback_results["monitoring"] = {"rolled_back": False, "error": str(e)}

    if "hardening" in steps_completed:
        try:
            rollback_results["hardening"] = await aws_account_baseline_hardening.rollback(
                parameters, execution_result.get("hardening", {}), connector
            )
        except Exception as e:
            logger.error("aws_account_full_baseline: hardening rollback raised: %s", e)
            rollback_results["hardening"] = {"rolled_back": False, "error": str(e)}

    all_ok = all(v.get("rolled_back", False) for v in rollback_results.values())
    return {
        "rolled_back": all_ok,
        "rollback_results": rollback_results,
    }
```

**Verification:** `grep -n "def execute\|def rollback\|ROLLBACK_CAPABILITY" backend/app/connectors/executors/aws/aws_account_full_baseline.py` should show both functions and the capability constant.

---

## Task 2 — Wiring: change_type_definition + ChangeType enum + DB migration + catalog entry

### 2a — change_type_definition JSON

**File to create:** `backend/app/connectors/change_type_definitions/aws_account_full_baseline.json`

- [ ] Create the file:

```json
{
  "change_type": "aws_account_full_baseline",
  "display_name": "AWS Account Full Baseline",
  "steps": [
    {
      "generic_action": "aws_account_full_baseline",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "aws_account_full_baseline",
  "rollback_connector_type": "aws"
}
```

---

### 2b — ChangeType enum

**File to modify:** `backend/app/models/change_request.py`

- [ ] Find the block (around line 168–174):
  ```python
      aws_account_baseline_monitoring = "aws_account_baseline_monitoring"
      aws_account_baseline_hardening = "aws_account_baseline_hardening"
      aws_iam_role_baseline = "aws_iam_role_baseline"
  ```
- [ ] Add one line immediately after `aws_iam_role_baseline`:
  ```python
      aws_account_full_baseline = "aws_account_full_baseline"
  ```

The block should read:
```python
    # Cloud account baseline monitoring
    aws_account_baseline_monitoring = "aws_account_baseline_monitoring"
    aws_account_baseline_hardening = "aws_account_baseline_hardening"
    aws_iam_role_baseline = "aws_iam_role_baseline"
    aws_account_full_baseline = "aws_account_full_baseline"
```

---

### 2c — Alembic migration

**File to create:** `backend/alembic/versions/20260805_add_aws_account_full_baseline_change_type.py`

- [ ] Create the file:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add aws_account_full_baseline change type

Revision ID: 20260805_full_baseline
Revises: 20260803
Create Date: 2026-08-05
"""
from alembic import op

revision = '20260805_full_baseline'
down_revision = '20260803'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'aws_account_full_baseline'")


def downgrade():
    pass
```

**Note:** `down_revision = '20260803'` matches the most recent migration file `20260803_add_container_image_transfer_change_type.py`.

---

### 2d — Catalog entry

**File to modify:** `backend/app/connectors/catalog/aws.json`

- [ ] Locate the `aws_iam_role_baseline` entry (ends around line 2391). Insert the new entry **after** the closing `}` of `aws_iam_role_baseline` and **before** the `dns_zone_migrate` entry. The insertion point is the comma after the `aws_iam_role_baseline` closing brace.

- [ ] Add this JSON object into the `"actions"` array:

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
      "rollback_action": "aws_account_full_baseline",
      "rollback_connector_type": "aws",
      "estimated_duration_seconds": 600,
      "blast_radius_hint": "account_policy",
      "safety_notes": [
        "Idempotent — safe to run multiple times",
        "Enabling GuardDuty and SecurityHub incurs AWS charges (negligible for most accounts)",
        "IAM password policy applies to all IAM users in the account"
      ],
      "smoke_verified": false
    },
```

**Verification:** `python -c "import json; json.load(open('backend/app/connectors/catalog/aws.json'))"` must exit 0 (run from the `backend/` directory or adjust path).

---

## Task 3 — Smoke test

**File to create:** `backend/tests/smoke/test_smoke_aws_account_full_baseline.py`

- [ ] Create the file:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: AWS Account Full Baseline

Runs the aws_account_full_baseline CR lifecycle against the live platform AWS account:
  Phase 1: Setup  — verify AWS connectivity via sts.get_caller_identity()
  Phase 2: Execute — full CR lifecycle; assert all 3 sub-steps complete; spot-check AWS state
  Phase 3: Rollback — trigger rollback; assert rolled_back; spot-check IAM role cleanup
  Phase 4: (none — rollback is the teardown)

Run from EC2 runner on Tailscale:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_aws_account_full_baseline.py -v -s

Must NOT run from local Docker — see feedback_smoke_test_environment memory note.
"""

import asyncio
import hashlib
import os

import boto3
import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_STATE: dict = {}


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set")
    return val


async def _get_aws_creds() -> tuple[dict, str]:
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Connector).where(Connector.connector_type == "aws"))
        connector = r.scalars().first()
        if not connector:
            pytest.skip("No AWS connector registered")
        cr = await db.execute(
            select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
        )
        cc = cr.scalars().first()
        backend = get_secret_backend()
        creds = backend.decrypt_json(cc.credentials_encrypted)
        return creds, str(connector.id)


def _boto_client(service: str, creds: dict, region: str = "us-east-1"):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("session_token"),
        region_name=region,
    )


async def _get_jwt(api_token: str) -> str:
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from app.services.auth_service import create_access_token
    from sqlalchemy import select

    token_hash = hashlib.sha256(api_token.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked == False,  # noqa: E712
            )
        )
        tok = r.scalar_one()
        return create_access_token(subject=str(tok.user_id))


async def _plan_and_approve(token: str, cr_id: str, comment: str = "smoke self-approval") -> None:
    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": comment},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 600) -> dict:
    interval = 15
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200
            detail = r.json()
        if detail.get("status") in terminal:
            return detail
    pytest.fail(f"CR {cr_id} did not reach {terminal} within {timeout}s")


def _extract_exec_result(detail: dict) -> dict:
    """Extract the execution_result dict from a CR detail response."""
    runs = detail.get("execution_runs", [])
    for run in reversed(runs):
        steps = run.get("result", {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result", {})
    return detail.get("execution_result", {})


# ── Phase 1: Setup ────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_FULL_BASELINE")
async def test_01_setup_verify_connectivity():
    """Confirm AWS creds are accessible and sts.get_caller_identity() works."""
    token = _env("API_TOKEN")
    creds, connector_id = await _get_aws_creds()

    loop = asyncio.get_event_loop()
    identity = await loop.run_in_executor(
        None, lambda: _boto_client("sts", creds).get_caller_identity()
    )
    assert "Account" in identity, f"Unexpected identity response: {identity}"

    _STATE["token"] = token
    _STATE["connector_id"] = connector_id
    _STATE["creds"] = creds
    _STATE["account_id"] = identity["Account"]

    print(f"\n[setup] AWS account: {identity['Account']}, ARN: {identity['Arn']}")


# ── Phase 2: Execute ──────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_FULL_BASELINE")
async def test_02_execute_full_baseline():
    """Create and execute the aws_account_full_baseline CR. Assert all 3 sub-steps complete."""
    token = _STATE["token"]
    connector_id = _STATE["connector_id"]
    creds = _STATE["creds"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        payload = {
            "change_type": "aws_account_full_baseline",
            "connector_id": connector_id,
            "title": "Smoke: AWS Account Full Baseline",
            "desired_outcome": {
                "role_name_prefix": "NexplaneSmoke",
                "dry_run": False,
            },
        }
        r = await client.post("/change-requests", json=payload, headers=headers)
        assert r.status_code in (200, 201), f"CR create failed: {r.text}"
        cr_id = r.json()["id"]
        _STATE["cr_id"] = cr_id
        print(f"\n[execute] CR created: {cr_id}")

    await _plan_and_approve(token, cr_id, comment="aws_account_full_baseline smoke self-approval")

    # Poll until completed or failed — allow up to 10 minutes (monitoring is slow)
    detail = await _poll_cr(token, cr_id, {"completed", "failed", "error"}, timeout=600)
    assert detail["status"] == "completed", (
        f"CR did not complete. Status: {detail['status']}\n"
        f"execution_result: {_extract_exec_result(detail)}"
    )

    exec_result = _extract_exec_result(detail)
    _STATE["exec_result"] = exec_result

    # Assert all three sub-steps completed
    steps_completed = exec_result.get("steps_completed", [])
    assert "hardening" in steps_completed, f"hardening step missing from steps_completed: {steps_completed}"
    assert "monitoring" in steps_completed, f"monitoring step missing from steps_completed: {steps_completed}"
    assert "iam_roles" in steps_completed, f"iam_roles step missing from steps_completed: {steps_completed}"

    # Assert no sub-step is outright failed
    hardening = exec_result.get("hardening", {})
    monitoring = exec_result.get("monitoring", {})
    iam_roles = exec_result.get("iam_roles", {})

    assert hardening.get("status") != "failed" and not hardening.get("failed"), (
        f"hardening sub-step failed: {hardening}"
    )
    assert monitoring.get("status") != "failed" and not monitoring.get("failed"), (
        f"monitoring sub-step failed: {monitoring}"
    )
    assert iam_roles.get("status") != "failed", f"iam_roles sub-step failed: {iam_roles}"

    print(f"[execute] steps_completed: {steps_completed}")
    print(f"[execute] hardening newly_applied: {hardening.get('summary', {}).get('newly_applied', hardening.get('newly_applied', 'n/a'))}")
    print(f"[execute] monitoring newly_enabled: {monitoring.get('summary', {}).get('newly_enabled', 'n/a')}")
    print(f"[execute] iam_roles created_roles: {iam_roles.get('created_roles', 'n/a')}")


@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_FULL_BASELINE")
async def test_03_spot_check_aws_state():
    """Spot-check live AWS state: IAM password policy min length >= 14, CloudTrail multi-region trail exists."""
    creds = _STATE["creds"]
    loop = asyncio.get_event_loop()

    # Spot-check 1: IAM password policy
    iam = _boto_client("iam", creds)
    pp = await loop.run_in_executor(
        None, lambda: iam.get_account_password_policy()["PasswordPolicy"]
    )
    assert pp.get("MinimumPasswordLength", 0) >= 14, (
        f"IAM password policy MinimumPasswordLength is {pp.get('MinimumPasswordLength')} — expected >= 14"
    )
    print(f"[spot-check] IAM password policy MinimumPasswordLength: {pp['MinimumPasswordLength']} ✓")

    # Spot-check 2: CloudTrail multi-region trail
    ct = _boto_client("cloudtrail", creds)
    trails = await loop.run_in_executor(
        None, lambda: ct.describe_trails(includeShadowTrails=False).get("trailList", [])
    )
    multi_region_trails = [t for t in trails if t.get("IsMultiRegionTrail")]
    assert len(multi_region_trails) >= 1, (
        f"No multi-region CloudTrail trail found. Trails: {[t.get('Name') for t in trails]}"
    )
    print(f"[spot-check] CloudTrail multi-region trail: {multi_region_trails[0].get('Name')} ✓")


# ── Phase 3: Rollback ─────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_FULL_BASELINE")
async def test_04_rollback():
    """Trigger CR rollback and assert it reaches rolled_back status."""
    token = _STATE["token"]
    cr_id = _STATE["cr_id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/rollback", headers=headers)
        assert r.status_code == 200, f"rollback trigger failed: {r.text}"
        print(f"\n[rollback] rollback triggered for CR {cr_id}")

    # Poll until rolled_back or failed — allow 10 minutes (monitoring rollback is slow)
    detail = await _poll_cr(token, cr_id, {"rolled_back", "rollback_failed", "error"}, timeout=600)
    assert detail["status"] == "rolled_back", (
        f"CR rollback did not complete. Status: {detail['status']}\n"
        f"detail: {_extract_exec_result(detail)}"
    )
    print("[rollback] CR status: rolled_back ✓")


@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_FULL_BASELINE")
async def test_05_spot_check_rollback_cleanup():
    """Spot-check that NexplaneSmoke-prefixed IAM roles were deleted by rollback."""
    creds = _STATE["creds"]
    loop = asyncio.get_event_loop()

    iam = _boto_client("iam", creds)
    prefix = "NexplaneSmoke"
    roles = await loop.run_in_executor(
        None,
        lambda: iam.list_roles(PathPrefix="/").get("Roles", [])
    )
    smoke_roles = [r["RoleName"] for r in roles if r["RoleName"].startswith(prefix)]
    assert len(smoke_roles) == 0, (
        f"Expected NexplaneSmoke-prefixed roles to be deleted after rollback, "
        f"but found: {smoke_roles}"
    )
    print(f"[spot-check] NexplaneSmoke-prefixed IAM roles deleted ✓")
```

**Run command (from EC2 runner, inside the backend container):**
```bash
docker exec nexplane-backend-1 bash -c "API_TOKEN=\$API_TOKEN pytest tests/smoke/test_smoke_aws_account_full_baseline.py -v -s 2>&1"
```

**Pass criteria:**
- All 5 test functions pass.
- `steps_completed` contains `["hardening", "monitoring", "iam_roles"]`.
- IAM password policy MinimumPasswordLength >= 14.
- At least one multi-region CloudTrail trail exists.
- CR reaches `rolled_back` status.
- No `NexplaneSmoke`-prefixed IAM roles remain after rollback.

---

## Execution order

1. Task 1 (executor) — standalone, no dependencies.
2. Task 2a (CTD JSON) — standalone.
3. Task 2b (ChangeType enum) — standalone.
4. Task 2c (migration) — standalone; run `alembic upgrade head` on EC2 after creating.
5. Task 2d (catalog) — validate JSON after edit.
6. Task 3 (smoke) — only after Tasks 1–2 are deployed to EC2 (`git pull` on EC2, restart backend container).

**Deploy sequence for Tasks 1–2:**
```bash
# On laptop
git add backend/app/connectors/executors/aws/aws_account_full_baseline.py \
        backend/app/connectors/change_type_definitions/aws_account_full_baseline.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/aws.json \
        backend/alembic/versions/20260805_add_aws_account_full_baseline_change_type.py
git commit -m "feat: aws_account_full_baseline CR type"
git push origin master

# On EC2
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /home/ec2-user/nexplane
git pull
docker exec nexplane-backend-1 alembic upgrade head
docker compose restart backend
```

**Deploy Task 3 (smoke file):**
```bash
# On laptop
git add backend/tests/smoke/test_smoke_aws_account_full_baseline.py
git commit -m "test: smoke test for aws_account_full_baseline"
git push origin master

# On EC2
git pull
# (no restart needed — tests run inside the container directly)
```

---

## Files summary

| Action | Path |
|--------|------|
| CREATE | `backend/app/connectors/executors/aws/aws_account_full_baseline.py` |
| CREATE | `backend/app/connectors/change_type_definitions/aws_account_full_baseline.json` |
| MODIFY | `backend/app/models/change_request.py` — add `aws_account_full_baseline` enum value |
| MODIFY | `backend/app/connectors/catalog/aws.json` — add catalog entry after `aws_iam_role_baseline` |
| CREATE | `backend/alembic/versions/20260805_add_aws_account_full_baseline_change_type.py` |
| CREATE | `backend/tests/smoke/test_smoke_aws_account_full_baseline.py` |
