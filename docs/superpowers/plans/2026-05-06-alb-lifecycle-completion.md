# ALB Lifecycle Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the ALB lifecycle feature by adding 4 missing change types (target_group_delete, listener_delete, register_targets, deregister_targets) plus a Phase W smoke test that exercises the full ALB create → target group → register → listener → verify → deregister → rollback cycle.

**Architecture:** All 8 ALB executors already exist in `backend/app/connectors/executors/aws/`. Missing pieces are: one new executor (`delete_listener.py`), 4 CT definition JSONs, 4 ChangeType enum entries, one Alembic migration, and the Phase W smoke test function in `test_aws_live.py`. The smoke test uses a rollback stack (LIFO list of (cr_id, label) tuples) with boto3 for verification at each step.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, boto3 (elbv2), pytest

---

## Files

**Create:**
- `backend/app/connectors/executors/aws/delete_listener.py`
- `backend/app/connectors/change_type_definitions/target_group_delete.json`
- `backend/app/connectors/change_type_definitions/listener_delete.json`
- `backend/app/connectors/change_type_definitions/register_targets.json`
- `backend/app/connectors/change_type_definitions/deregister_targets.json`
- `backend/alembic/versions/031_add_alb_register_delete_types.py`

**Modify:**
- `backend/app/models/change_request.py` — add 4 enum values
- `backend/tests/smoke/test_aws_live.py` — add `run_phase_w`, wire into `main()`

---

### Task 1: Add missing ALB change types + delete_listener executor

**Files:**
- Create: `backend/app/connectors/executors/aws/delete_listener.py`
- Create: `backend/app/connectors/change_type_definitions/target_group_delete.json`
- Create: `backend/app/connectors/change_type_definitions/listener_delete.json`
- Create: `backend/app/connectors/change_type_definitions/register_targets.json`
- Create: `backend/app/connectors/change_type_definitions/deregister_targets.json`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/031_add_alb_register_delete_types.py`

- [ ] **Step 1: Create `delete_listener.py` executor**

Create `backend/app/connectors/executors/aws/delete_listener.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    listener_arn = parameters.get("listener_arn", "")

    if not creds:
        return {"action": "delete_listener", "listener_arn": listener_arn, "mock": True}

    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, "elbv2")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: elbv2.delete_listener(ListenerArn=listener_arn))
    return {
        "action": "delete_listener",
        "listener_arn": listener_arn,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "listener deletion cannot be reversed automatically"}
```

- [ ] **Step 2: Create the 4 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/target_group_delete.json`:
```json
{
  "change_type": "target_group_delete",
  "display_name": "Delete Target Group",
  "steps": [
    {"generic_action": "delete_target_group", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/listener_delete.json`:
```json
{
  "change_type": "listener_delete",
  "display_name": "Delete ALB Listener",
  "steps": [
    {"generic_action": "delete_listener", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/register_targets.json`:
```json
{
  "change_type": "register_targets",
  "display_name": "Register Targets with Target Group",
  "steps": [
    {"generic_action": "register_targets", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "deregister_targets",
  "rollback_connector_type": "aws"
}
```

Create `backend/app/connectors/change_type_definitions/deregister_targets.json`:
```json
{
  "change_type": "deregister_targets",
  "display_name": "Deregister Targets from Target Group",
  "steps": [
    {"generic_action": "deregister_targets", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Add the 4 new ChangeType enum values**

In `backend/app/models/change_request.py`, find the ALB listener_modify entry (around line 155):
```python
    listener_modify = "listener_modify"
```

Add the 4 new values immediately after it:
```python
    listener_modify = "listener_modify"
    target_group_delete = "target_group_delete"
    listener_delete = "listener_delete"
    register_targets = "register_targets"
    deregister_targets = "deregister_targets"
```

- [ ] **Step 4: Create migration 031**

Create `backend/alembic/versions/031_add_alb_register_delete_types.py`:

```python
"""add ALB register/delete change types

Revision ID: 031
Revises: 030
Create Date: 2026-05-06
"""
from alembic import op

revision = '031'
down_revision = '030'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'target_group_delete',
        'listener_delete',
        'register_targets',
        'deregister_targets',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass  # PostgreSQL does not support removing enum values
```

- [ ] **Step 5: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected output contains: `Running upgrade 030 -> 031, add ALB register/delete change types`

- [ ] **Step 6: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/aws/delete_listener.py \
        backend/app/connectors/change_type_definitions/target_group_delete.json \
        backend/app/connectors/change_type_definitions/listener_delete.json \
        backend/app/connectors/change_type_definitions/register_targets.json \
        backend/app/connectors/change_type_definitions/deregister_targets.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/031_add_alb_register_delete_types.py
git commit -m "feat(alb): add target_group_delete, listener_delete, register_targets, deregister_targets change types + migration 031"
```

---

### Task 2: Add Phase W — ALB lifecycle smoke test

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase W tests the full ALB lifecycle:
1. Look up VPC/subnets/SG from Phase A EC2 instance via boto3
2. Create ALB → push to rollback stack
3. Create target group → push to rollback stack
4. Register Phase A instance as target
5. Create listener → push to rollback stack
6. Verify target is registered (boto3 describe_target_health)
7. Modify listener port
8. Verify port changed (boto3)
9. Deregister target
10. Verify zero targets (boto3)
11. Finally: rollback stack handles listener → target group → ALB deletion

- [ ] **Step 1: Add `run_phase_w` function**

In `backend/tests/smoke/test_aws_live.py`, after the `run_phase_v` function (around line 1800, search for `def run_phase_v`), add this new function:

```python
def run_phase_w(client: NexplaneClient, cloud_account_id: str, phase_a_result: dict) -> None:
    """Phase W: ALB lifecycle — create ALB + target group + listener, register targets, verify, rollback."""
    print("\n[Phase W] ALB Lifecycle")

    instance_id = phase_a_result["instance_id"]
    rollback_stack: list[tuple[str, str]] = []

    # Look up VPC/subnets/SG from the Phase A EC2 instance
    ec2 = _get_aws_boto3_client("ec2")
    elbv2 = _get_aws_boto3_client("elbv2")
    if not ec2 or not elbv2:
        fail("Phase W requires AWS boto3 client with ec2 and elbv2 access")

    inst_resp = ec2.describe_instances(InstanceIds=[instance_id])
    inst_data = inst_resp["Reservations"][0]["Instances"][0]
    vpc_id = inst_data["VpcId"]
    sg_ids = [sg["GroupId"] for sg in inst_data["SecurityGroups"]]

    # ALB requires ≥2 subnets in different AZs
    subnets_resp = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    subnet_ids: list[str] = []
    seen_azs: set[str] = set()
    for s in subnets_resp["Subnets"]:
        az = s["AvailabilityZone"]
        if az not in seen_azs:
            subnet_ids.append(s["SubnetId"])
            seen_azs.add(az)
        if len(subnet_ids) == 2:
            break
    if len(subnet_ids) < 2:
        fail(f"Phase W requires ≥2 subnets in different AZs in VPC {vpc_id}, found {len(subnet_ids)}")

    alb_name = "nexplane-smoke-alb"
    tg_name = "nexplane-smoke-tg"

    try:
        # 1. Create ALB
        cr = client.run_cr(
            "[Phase W] create ALB", "alb_create", cloud_account_id,
            {
                "name": alb_name,
                "scheme": "internet-facing",
                "lb_type": "application",
                "subnets": subnet_ids,
                "security_groups": sg_ids,
            },
        )
        rollback_stack.append((cr["id"], "alb_create"))

        lb_resp = elbv2.describe_load_balancers(Names=[alb_name])
        lb_arn = lb_resp["LoadBalancers"][0]["LoadBalancerArn"]
        log(f"ALB created: {lb_arn}")

        # 2. Create target group
        cr = client.run_cr(
            "[Phase W] create target group", "target_group_create", cloud_account_id,
            {
                "name": tg_name,
                "protocol": "HTTP",
                "port": 80,
                "vpc_id": vpc_id,
                "target_type": "instance",
            },
        )
        rollback_stack.append((cr["id"], "target_group_create"))

        tg_resp = elbv2.describe_target_groups(Names=[tg_name])
        tg_arn = tg_resp["TargetGroups"][0]["TargetGroupArn"]
        log(f"Target group created: {tg_arn}")

        # 3. Register Phase A instance as target
        targets = [{"Id": instance_id, "Port": 80}]
        cr = client.run_cr(
            "[Phase W] register targets", "register_targets", cloud_account_id,
            {"tg_arn": tg_arn, "targets": targets},
        )
        rollback_stack.append((cr["id"], "register_targets"))
        log(f"Instance {instance_id} registered as target")

        # 4. Create listener on port 80 forwarding to the target group
        cr = client.run_cr(
            "[Phase W] create listener", "listener_create", cloud_account_id,
            {
                "lb_arn": lb_arn,
                "protocol": "HTTP",
                "port": 80,
                "default_target_group_arn": tg_arn,
            },
        )
        rollback_stack.append((cr["id"], "listener_create"))

        listeners_resp = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
        listener_arn = listeners_resp["Listeners"][0]["ListenerArn"]
        log(f"Listener created: {listener_arn}")

        # 5. Verify target is registered via boto3
        health_resp = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        registered_ids = [t["Target"]["Id"] for t in health_resp["TargetHealthDescriptions"]]
        assert instance_id in registered_ids, \
            f"Instance {instance_id} not in registered targets: {registered_ids}"
        log("Target registration verified (boto3 describe_target_health)")

        # 6. Modify listener — change port to 8080
        client.run_cr(
            "[Phase W] modify listener port", "listener_modify", cloud_account_id,
            {"listener_arn": listener_arn, "port": 8080, "protocol": "HTTP"},
        )

        updated_listeners = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
        updated_port = updated_listeners["Listeners"][0]["Port"]
        assert updated_port == 8080, f"Listener port not updated: expected 8080, got {updated_port}"
        log("Listener port modified to 8080 (boto3 verified)")

        # 7. Deregister targets — exercise the deregister_targets CR
        client.run_cr(
            "[Phase W] deregister targets", "deregister_targets", cloud_account_id,
            {"tg_arn": tg_arn, "targets": targets},
        )
        # Pop register_targets from rollback stack (already deregistered)
        rollback_stack.pop()

        health_after = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        remaining = [t["Target"]["Id"] for t in health_after["TargetHealthDescriptions"]]
        assert instance_id not in remaining, \
            f"Instance {instance_id} still registered after deregister: {remaining}"
        log("Target deregistered and verified (boto3 describe_target_health)")

        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        print("  [Phase W cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete ALB and TG via boto3 if still present
        try:
            if elbv2:
                try:
                    lb_resp2 = elbv2.describe_load_balancers(Names=[alb_name])
                    if lb_resp2.get("LoadBalancers"):
                        remaining_lb_arn = lb_resp2["LoadBalancers"][0]["LoadBalancerArn"]
                        # Delete listeners first
                        for l in elbv2.describe_listeners(LoadBalancerArn=remaining_lb_arn).get("Listeners", []):
                            elbv2.delete_listener(ListenerArn=l["ListenerArn"])
                        elbv2.delete_load_balancer(LoadBalancerArn=remaining_lb_arn)
                        print(f"  Safety net: deleted ALB {alb_name}")
                except Exception:
                    pass
                try:
                    tg_resp2 = elbv2.describe_target_groups(Names=[tg_name])
                    if tg_resp2.get("TargetGroups"):
                        elbv2.delete_target_group(TargetGroupArn=tg_resp2["TargetGroups"][0]["TargetGroupArn"])
                        print(f"  Safety net: deleted target group {tg_name}")
                except Exception:
                    pass
        except Exception:
            pass
```

- [ ] **Step 2: Update the docstring at the top of the file**

Find the phase descriptions in the module docstring (around line 18) and add:
```
    W  ALB lifecycle: create ALB + target group + listener, register EC2 target, verify health, deregister, rollback
```

- [ ] **Step 3: Wire Phase W into `main()`**

In `main()`, after the Phase V block (around line 1863), add:
```python
        if "W" in phases:
            if phase_a_result is None:
                fail("Phase W requires Phase A to have run first")
            run_phase_w(client, cloud_account_id, phase_a_result)
```

- [ ] **Step 4: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Verify Phase W can be parsed (dry parse check)**

```bash
docker exec nexplane-backend-1 python -c "
import sys
sys.path.insert(0, '/app/tests/smoke')
import test_aws_live
print('Phase W function:', test_aws_live.run_phase_w)
print('OK')
"
```

Expected: prints the function reference and `OK`.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(alb): add Phase W smoke test — ALB + target group + listener lifecycle with rollback stack"
```

---

## Self-review

**Spec coverage:**
- ✅ `target_group_delete` change type + executor wiring — Task 1
- ✅ `listener_delete` change type + executor — Task 1
- ✅ `register_targets` change type — Task 1
- ✅ `deregister_targets` change type — Task 1
- ✅ Migration 031 for all 4 new types — Task 1
- ✅ Phase W: create ALB → target group → register → listener — Task 2
- ✅ Phase W: boto3 verify target health — Task 2
- ✅ Phase W: modify listener, verify port — Task 2
- ✅ Phase W: deregister targets, verify — Task 2
- ✅ Phase W: rollback stack (listener → tg → alb) — Task 2
- ✅ Phase W: boto3 safety net in finally — Task 2
- ✅ Phase W wired into main() — Task 2
- ✅ Docstring updated — Task 2

**Placeholder scan:** None. All steps contain complete code.

**Type consistency:**
- `rollback_stack: list[tuple[str, str]]` — same pattern as all other phases
- `_get_aws_boto3_client("elbv2")` — same helper used throughout test file
- `client.run_cr(title, change_type, asset_id, desired_outcome)` — same signature throughout
- `client.rollback_cr(cr_id, label)` — same signature throughout
