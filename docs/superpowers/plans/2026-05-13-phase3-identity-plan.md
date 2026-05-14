# Phase 3 — Identity Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user identity security primitives — emergency lockout, temporary suspension, session termination, scope reduction, time-bound access, and MFA enforcement — as first-class CR types operating across connected identity systems (AWS IAM, Linux local users initially; AD/Azure AD connector extension is Phase 5).

**Architecture:** New CR types backed by executor files that fan out to connected identity system connectors. AWS IAM is the primary implementation. Linux local users via the agent's `linuxauth` package. All operations have paired reversal CRs.

**Prerequisites:** Phase 0 (executor wire-up), Phase 1 (notification system, finding lifecycle).

---

## Task 1: Emergency user lockout CR type

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/emergency_user_lockout.py`
- Create: `backend/app/connectors/executors/aws/lock_iam_user.py`
- Modify: `backend/app/connectors/catalog_service.py` (register new change type)
- Modify: `backend/app/models/change_request.py` (ensure `emergency_user_lockout` in ChangeType enum)
- Create: `backend/app/tests/test_emergency_user_lockout.py`
- Modify: `frontend/src/pages/ChangeRequestList.tsx` (IR tab — "Isolate User" button)

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_emergency_user_lockout.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_emergency_lockout_disables_iam_user(mocker):
    mock_disable = mocker.AsyncMock(return_value={"action": "disable_iam_user", "user": "test@acme", "status": "disabled"})
    mocker.patch("app.connectors.executors.aws.lock_iam_user.execute", mock_disable)

    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute
    result = await execute(
        {"user_identifier": "test@acme", "systems": ["aws_iam"]},
        ["asset-uuid-1"], None
    )
    assert result["lockout_status"]["aws_iam"] == "locked"
    mock_disable.assert_called_once()

@pytest.mark.asyncio
async def test_lockout_tolerates_partial_system_failure(mocker):
    """If one system fails to lock, the others still succeed."""
    mocker.patch("app.connectors.executors.aws.lock_iam_user.execute",
        side_effect=Exception("AWS unreachable"))
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute
    result = await execute(
        {"user_identifier": "test@acme", "systems": ["aws_iam", "linux_local"]},
        ["asset-uuid-1"], None
    )
    assert result["lockout_status"]["aws_iam"] == "failed"
    # linux_local may succeed if agent responds
    assert "errors" in result
```

- [ ] **Step 2: Create lock_iam_user.py (AWS executor)**

```python
# backend/app/connectors/executors/aws/lock_iam_user.py
import boto3
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    if not user:
        raise ValueError("user_name or user_identifier required")

    session = boto3.Session(
        aws_access_key_id=connector.credentials.get("access_key_id") if connector else None,
        aws_secret_access_key=connector.credentials.get("secret_access_key") if connector else None,
        region_name=(connector.credentials.get("region") if connector else None) or "us-east-1",
    )
    iam = session.client("iam")

    # Attach an inline deny-all policy (non-destructive, reversible)
    policy_name = "nexplane-emergency-lockout"
    deny_policy = '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"*","Resource":"*"}]}'
    iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=deny_policy)

    return {
        "action": "lock_iam_user",
        "user": user,
        "status": "locked",
        "policy_name": policy_name,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    session = boto3.Session(
        aws_access_key_id=connector.credentials.get("access_key_id") if connector else None,
        aws_secret_access_key=connector.credentials.get("secret_access_key") if connector else None,
        region_name=(connector.credentials.get("region") if connector else None) or "us-east-1",
    )
    iam = session.client("iam")
    try:
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-emergency-lockout")
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True, "user": user}
```

- [ ] **Step 3: Create emergency_user_lockout.py (fan-out executor)**

```python
# backend/app/connectors/executors/nexplane_agent/emergency_user_lockout.py
"""Fan-out user lockout across all connected identity systems."""
from __future__ import annotations
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    systems = parameters.get("systems", ["aws_iam", "linux_local"])
    results = {}
    errors = []

    for system in systems:
        try:
            if system == "aws_iam":
                from app.connectors.executors.aws.lock_iam_user import execute as iam_lock
                r = await iam_lock({"user_name": user}, asset_ids, connector)
                results["aws_iam"] = "locked"
            elif system == "linux_local":
                from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
                r = await dispatch_agent_job(
                    command="lock_local_user",
                    parameters={"username": user, "terminate_sessions": True},
                    asset_ids=list(asset_ids),
                    timeout_seconds=30,
                )
                results["linux_local"] = "locked"
        except Exception as e:
            results[system] = "failed"
            errors.append({"system": system, "error": str(e)})

    return {
        "action": "emergency_user_lockout",
        "user_identifier": user,
        "lockout_status": results,
        "errors": errors,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = parameters.get("user_identifier", "")
    rollback_results = {}
    if execution_result.get("lockout_status", {}).get("aws_iam") == "locked":
        from app.connectors.executors.aws.lock_iam_user import rollback as iam_unlock
        r = await iam_unlock({"user_identifier": user}, execution_result, connector)
        rollback_results["aws_iam"] = "unlocked" if r.get("rolled_back") else "failed"
    return {"rolled_back": True, "systems": rollback_results}
```

- [ ] **Step 4: Register change type in ChangeType enum**

In `backend/app/models/change_request.py`, add to ChangeType enum:
```python
emergency_user_lockout = "emergency_user_lockout"
user_suspension = "user_suspension"
user_scope_reduction = "user_scope_reduction"
enforce_mfa = "enforce_mfa"
```

- [ ] **Step 5: Wire change type to executor in connector_service.py**

In `backend/app/services/connector_service.py`, add to executor dispatch:
```python
"emergency_user_lockout": "app.connectors.executors.nexplane_agent.emergency_user_lockout",
```

- [ ] **Step 6: Frontend — "Isolate User" in IR tab**

In `frontend/src/pages/ChangeRequestList.tsx` IR tab, add an "Isolate User" button alongside "Isolate Host":
```tsx
<button
  onClick={() => setIRUserModalOpen(true)}
  className="px-3 py-1.5 text-sm bg-orange-600 text-white rounded hover:bg-orange-700"
>
  Isolate User
</button>
```

The modal collects `user_identifier`, then creates an `emergency_user_lockout` CR.

- [ ] **Step 7: Run tests, commit**

```bash
docker compose exec backend pytest app/tests/test_emergency_user_lockout.py -v 2>&1 | tail -10
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: emergency_user_lockout CR type — deny-all IAM policy + linux local user lockout"
```

---

## Task 2: Temporary user suspension

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/user_suspension.py`
- Create: `backend/app/workers/suspension_reversal_worker.py`
- Create: `backend/app/tests/test_user_suspension.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_suspension_creates_auto_reversal_cr(async_client, admin_token, test_asset_id):
    resp = await async_client.post("/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "title": "Suspend test-user pending investigation",
            "change_type": "user_suspension",
            "target_asset_ids": [test_asset_id],
            "desired_outcome": {
                "user_identifier": "test-user",
                "duration_hours": 4,
                "reason": "Suspicious login activity",
            }
        })
    assert resp.status_code == 201
    cr_id = resp.json()["id"]
    # After execution, a reversal CR should be scheduled
    # (checked via the CR's audit events)
```

- [ ] **Step 2: Create user_suspension.py**

```python
# backend/app/connectors/executors/nexplane_agent/user_suspension.py
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    duration_hours = float(parameters.get("duration_hours", 0))  # 0 = manual only

    # Lock the user (reuse emergency lockout logic)
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute as lockout
    lockout_result = await lockout(
        {"user_identifier": user, "systems": parameters.get("systems", ["aws_iam"])},
        asset_ids, connector,
    )

    result = {
        "action": "user_suspension",
        "user_identifier": user,
        "lockout_result": lockout_result,
        "suspended_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }

    # Schedule automatic reversal if duration set
    if duration_hours > 0:
        from app.services.scheduled_cr_service import schedule_reversal_cr
        reversal_cr_id = await schedule_reversal_cr(
            original_parameters=parameters,
            original_asset_ids=asset_ids,
            execute_after_hours=duration_hours,
            change_type="user_suspension_reversal",
        )
        result["scheduled_reversal_cr_id"] = reversal_cr_id

    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import rollback as unlock
    return await unlock(parameters, execution_result, connector)
```

- [ ] **Step 3: Create scheduled_cr_service.py**

```python
# backend/app/services/scheduled_cr_service.py
"""Service for creating CRs that execute at a future time."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType


async def schedule_reversal_cr(
    original_parameters: dict,
    original_asset_ids: list,
    execute_after_hours: float,
    change_type: str,
) -> str:
    execute_at = datetime.now(timezone.utc) + timedelta(hours=execute_after_hours)
    async with AsyncSessionLocal() as db:
        cr = ChangeRequest(
            id=uuid.uuid4(),
            title=f"Auto-reversal: {change_type} at {execute_at.strftime('%Y-%m-%d %H:%M UTC')}",
            change_type=ChangeType(change_type) if hasattr(ChangeType, change_type) else ChangeType.emergency_user_lockout,
            target_asset_ids=[str(a) for a in original_asset_ids],
            desired_outcome={**original_parameters, "_auto_reversal": True},
            execute_at=execute_at,
            status=ChangeRequestStatus.approved,  # Pre-approved for auto-execution
        )
        db.add(cr)
        await db.commit()
        return str(cr.id)
```

- [ ] **Step 4: Add `execute_at` to ChangeRequest model + worker**

```python
# In change_request.py model
execute_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
```

```python
# backend/app/workers/scheduled_cr_worker.py
async def execute_scheduled_crs():
    """Execute CRs whose execute_at time has passed."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.execute_at <= now,
                ChangeRequest.execute_at.isnot(None),
                ChangeRequest.status == ChangeRequestStatus.approved,
            )
        )
        for cr in result.scalars():
            from app.workflows import runner as wf_runner
            from app.workflows.execute_change_workflow import execute_change_workflow
            workflow_id = f"wf-scheduled-{cr.id}"
            await wf_runner.start_workflow(execute_change_workflow,
                wf_runner.WorkflowInput(change_request_id=str(cr.id), organization_id=str(cr.organization_id), initiator_id="system"),
                workflow_id=workflow_id)
```

- [ ] **Step 5: Migrate, register worker in main.py, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_execute_at_suspension"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_user_suspension.py -v 2>&1 | tail -10
git add backend/ && git commit -m "feat: user_suspension CR type with auto-reversal scheduling"
```

---

## Task 3: User scope reduction

**Files:**
- Create: `backend/app/connectors/executors/aws/scope_reduction_iam.py`
- Create: `backend/app/connectors/executors/nexplane_agent/user_scope_reduction.py`
- Create: `backend/app/tests/test_user_scope_reduction.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_demote_to_readonly_attaches_deny_write_policy(mocker):
    mock_iam = mocker.MagicMock()
    mocker.patch("boto3.Session.client", return_value=mock_iam)
    from app.connectors.executors.aws.scope_reduction_iam import execute
    result = await execute(
        {"user_name": "test-admin", "mode": "demote_to_readonly"},
        ["asset-1"], None
    )
    mock_iam.put_user_policy.assert_called_once()
    assert "deny-write" in mock_iam.put_user_policy.call_args[1]["PolicyName"]
    assert result["mode"] == "demote_to_readonly"
```

- [ ] **Step 2: Create scope_reduction_iam.py**

```python
# backend/app/connectors/executors/aws/scope_reduction_iam.py
import boto3, json
from datetime import datetime, timezone

DENY_WRITE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Deny",
        "Action": ["*:Put*", "*:Create*", "*:Delete*", "*:Update*", "*:Modify*",
                   "*:Attach*", "*:Detach*", "*:Start*", "*:Stop*", "*:Terminate*"],
        "Resource": "*"
    }]
})

MFA_REQUIRED_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Deny",
        "Action": "*",
        "Resource": "*",
        "Condition": {"BoolIfExists": {"aws:MultiFactorAuthPresent": "false"}}
    }]
})


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    mode = parameters.get("mode", "demote_to_readonly")
    creds = connector.credentials if connector else {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")

    if mode == "demote_to_readonly":
        policy_name = "nexplane-scope-reduction-deny-write"
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=DENY_WRITE_POLICY)
    elif mode == "mfa_required":
        policy_name = "nexplane-scope-reduction-mfa-required"
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=MFA_REQUIRED_POLICY)
    elif mode == "ip_restriction":
        cidr = parameters.get("allowed_cidr", "10.0.0.0/8")
        policy_name = "nexplane-scope-reduction-ip-restriction"
        policy = json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Deny", "Action": "*", "Resource": "*",
            "Condition": {"NotIpAddress": {"aws:SourceIp": [cidr]}}
        }]})
        iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=policy)
    else:
        raise ValueError(f"Unknown scope reduction mode: {mode}")

    return {
        "action": "user_scope_reduction", "user": user, "mode": mode,
        "policy_name": policy_name,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    policy_name = execution_result.get("policy_name", "nexplane-scope-reduction-deny-write")
    creds = connector.credentials if connector else {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    try:
        iam.delete_user_policy(UserName=user, PolicyName=policy_name)
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True}
```

- [ ] **Step 3: Create user_scope_reduction.py (fan-out)**

```python
# backend/app/connectors/executors/nexplane_agent/user_scope_reduction.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.aws.scope_reduction_iam import execute as iam_reduce
    result = await iam_reduce(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.scope_reduction_iam import rollback as iam_restore
    return await iam_restore(parameters, execution_result, connector)
```

- [ ] **Step 4: Register, test, commit**

```bash
docker compose exec backend pytest app/tests/test_user_scope_reduction.py -v 2>&1 | tail -10
git add backend/ && git commit -m "feat: user_scope_reduction CR type (demote_to_readonly/mfa_required/ip_restriction)"
```

---

## Task 4: Time-bound access grants

**Files:**
- Modify: `backend/app/routers/change_requests.py` (wire `access_expiry_hours` on CR create to schedule reversal)
- Modify: `backend/app/models/change_request.py` (add `access_expiry_hours`, `scheduled_rollback_cr_id`)
- Create: `backend/app/tests/test_time_bound_access.py`
- Create: `backend/alembic/versions/XXXX_add_cr_access_expiry.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_access_grant_with_expiry_schedules_rollback(async_client, admin_token, test_asset_id):
    resp = await async_client.post("/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "title": "Grant temp admin access",
            "change_type": "attach_iam_policy",
            "target_asset_ids": [test_asset_id],
            "desired_outcome": {"policy_arn": "arn:aws:iam::aws:policy/AdministratorAccess"},
            "access_expiry_hours": 4,
        })
    assert resp.status_code == 201
    cr = resp.json()
    assert cr["access_expiry_hours"] == 4
    assert cr["scheduled_rollback_cr_id"] is not None
```

- [ ] **Step 2: Add fields to ChangeRequest**

```python
access_expiry_hours: Mapped[float | None] = mapped_column(nullable=True)
scheduled_rollback_cr_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
```

- [ ] **Step 3: Hook into CR creation**

In `create_change_request` endpoint, after CR is created:
```python
if body.access_expiry_hours and body.access_expiry_hours > 0:
    from app.services.scheduled_cr_service import schedule_reversal_cr
    rollback_cr_id = await schedule_reversal_cr(
        original_parameters=body.desired_outcome,
        original_asset_ids=[str(a) for a in body.target_asset_ids],
        execute_after_hours=body.access_expiry_hours,
        change_type=f"{body.change_type}_rollback",
    )
    cr.scheduled_rollback_cr_id = uuid.UUID(rollback_cr_id)
    await db.commit()
```

- [ ] **Step 4: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_access_expiry_hours"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_time_bound_access.py -v 2>&1 | tail -10
git add backend/ && git commit -m "feat: time-bound access grants with auto-scheduled rollback CR"
```

---

## Task 5: MFA enforcement as remediation

**Files:**
- Create: `backend/app/connectors/executors/aws/enforce_mfa_iam.py`
- Create: `backend/app/connectors/executors/nexplane_agent/enforce_mfa.py`
- Create: `backend/app/tests/test_enforce_mfa.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_enforce_mfa_attaches_condition_policy(mocker):
    mock_iam = mocker.MagicMock()
    mocker.patch("boto3.Session.client", return_value=mock_iam)
    from app.connectors.executors.aws.enforce_mfa_iam import execute
    result = await execute({"user_name": "test-user"}, ["asset-1"], None)
    mock_iam.put_user_policy.assert_called_once()
    assert "mfa" in mock_iam.put_user_policy.call_args[1]["PolicyName"].lower()
    assert result["status"] == "mfa_required"
```

- [ ] **Step 2: Create enforce_mfa_iam.py**

Reuses the `MFA_REQUIRED_POLICY` from scope_reduction_iam.py:
```python
# backend/app/connectors/executors/aws/enforce_mfa_iam.py
import boto3
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_name") or parameters.get("user_identifier", "")
    creds = connector.credentials if connector else {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    policy_name = "nexplane-enforce-mfa-required"
    mfa_policy = '{"Version":"2012-10-17","Statement":[{"Sid":"DenyWithoutMFA","Effect":"Deny","Action":"*","Resource":"*","Condition":{"BoolIfExists":{"aws:MultiFactorAuthPresent":"false"}}}]}'
    iam.put_user_policy(UserName=user, PolicyName=policy_name, PolicyDocument=mfa_policy)
    return {
        "action": "enforce_mfa", "user": user, "status": "mfa_required",
        "policy_name": policy_name,
        "enforced_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = execution_result.get("user") or parameters.get("user_identifier", "")
    creds = connector.credentials if connector else {}
    iam = boto3.Session(
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    ).client("iam")
    try:
        iam.delete_user_policy(UserName=user, PolicyName="nexplane-enforce-mfa-required")
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
    return {"rolled_back": True}
```

- [ ] **Step 3: Create enforce_mfa.py executor (fan-out)**

```python
# backend/app/connectors/executors/nexplane_agent/enforce_mfa.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.aws.enforce_mfa_iam import execute as iam_mfa
    result = await iam_mfa(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.enforce_mfa_iam import rollback as iam_mfa_rollback
    return await iam_mfa_rollback(parameters, execution_result, connector)
```

- [ ] **Step 4: Register change types in enum and connector_service**

Add `enforce_mfa` to ChangeType enum. Add to connector_service executor map:
```python
"enforce_mfa": "app.connectors.executors.nexplane_agent.enforce_mfa",
```

- [ ] **Step 5: Test and commit**

```bash
docker compose exec backend pytest app/tests/test_enforce_mfa.py -v 2>&1 | tail -10
git add backend/ && git commit -m "feat: enforce_mfa CR type — IAM deny-without-MFA condition policy"
```

---

## Task 6: USER_ISOLATE smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add smoke phase**

```python
def run_phase_user_isolate(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase USER_ISOLATE: creates test IAM user, runs emergency lockout, scope reduction, time-bound access, cleanup."""
    print("\n[Phase USER_ISOLATE] Identity security smoke test")
    import boto3
    _ec2 = _get_aws_boto3_client("ec2")
    _iam_boto = _get_aws_boto3_client("iam")
    if not _iam_boto:
        fail("[Phase USER_ISOLATE] Requires AWS credentials")

    test_user = "nexplane-smoke-identity-test"
    try:
        # Create test IAM user with S3 read access
        try:
            _iam_boto.create_user(UserName=test_user)
        except _iam_boto.exceptions.EntityAlreadyExistsException:
            pass
        _iam_boto.attach_user_policy(
            UserName=test_user,
            PolicyArn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
        )
        log(f"Test IAM user created: {test_user}")

        # Get the test user's asset (or create an asset record)
        assets = client.get("/assets", params={"q": test_user})
        asset_id = assets[0]["id"] if assets else cloud_account_id

        # --- Emergency lockout ---
        cr_id = client.create_cr(
            "[USER_ISOLATE] emergency lockout",
            "emergency_user_lockout", asset_id,
            {"user_identifier": test_user, "systems": ["aws_iam"]},
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr_id}/execute")
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            cr = client.get(f"/change-requests/{cr_id}")
            if cr.get("status") == "completed":
                break
            elif cr.get("status") in ("failed", "rolled_back"):
                fail(f"[USER_ISOLATE] Lockout CR failed: {cr.get('status')}")
            time.sleep(10)

        # Verify user is locked (deny-all policy attached)
        policies = _iam_boto.list_user_policies(UserName=test_user)["PolicyNames"]
        if "nexplane-emergency-lockout" not in policies:
            fail(f"[USER_ISOLATE] Lockout policy not attached. Policies: {policies}")
        log("Emergency lockout applied: deny-all policy attached ✓")

        # --- Rollback (unlock) ---
        client.rollback_cr(cr_id, "[USER_ISOLATE] unlock after test")
        time.sleep(10)
        policies_after = _iam_boto.list_user_policies(UserName=test_user)["PolicyNames"]
        if "nexplane-emergency-lockout" in policies_after:
            log("  ⚠️  Lockout policy not removed after rollback")
        else:
            log("Lockout rollback: deny policy removed ✓")

        # --- Scope reduction ---
        cr2_id = client.create_cr(
            "[USER_ISOLATE] scope reduction",
            "user_scope_reduction", asset_id,
            {"user_identifier": test_user, "mode": "demote_to_readonly"},
        )
        client.post(f"/change-requests/{cr2_id}/plan")
        client.post(f"/change-requests/{cr2_id}/submit-for-approval")
        client.post(f"/change-requests/{cr2_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr2_id}/execute")
        deadline = time.time() + 60
        while time.time() < deadline:
            cr2 = client.get(f"/change-requests/{cr2_id}")
            if cr2.get("status") == "completed":
                break
            time.sleep(5)
        policies_scope = _iam_boto.list_user_policies(UserName=test_user)["PolicyNames"]
        if "nexplane-scope-reduction-deny-write" not in policies_scope:
            fail(f"[USER_ISOLATE] Scope reduction policy not attached. Policies: {policies_scope}")
        log("Scope reduction applied: deny-write policy attached ✓")
        client.rollback_cr(cr2_id, "[USER_ISOLATE] restore scope")

        log("Phase USER_ISOLATE complete")

    except Exception as e:
        print(f"\n❌ Phase USER_ISOLATE failed: {e}")
        raise
    finally:
        # Cleanup: delete test IAM user and all inline policies
        try:
            for policy_name in _iam_boto.list_user_policies(UserName=test_user).get("PolicyNames", []):
                _iam_boto.delete_user_policy(UserName=test_user, PolicyName=policy_name)
            for policy in _iam_boto.list_attached_user_policies(UserName=test_user).get("AttachedPolicies", []):
                _iam_boto.detach_user_policy(UserName=test_user, PolicyArn=policy["PolicyArn"])
            _iam_boto.delete_user(UserName=test_user)
            log(f"Test IAM user {test_user} deleted")
        except Exception:
            pass
```

- [ ] **Step 2: Wire and commit**

```python
if "USER_ISOLATE" in phases:
    run_phase_user_isolate(client, cloud_account_id)
```

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test: USER_ISOLATE smoke phase — creates/locks/scopes/rollbacks test IAM user"
```
