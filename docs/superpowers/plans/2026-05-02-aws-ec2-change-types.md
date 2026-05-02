# AWS EC2 Instance Lifecycle Change Types — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six governed EC2 change types (stop, start, reboot, stop/start, launch, terminate) wired to the real AWS connector via eight new executor files.

**Architecture:** Each change type is a JSON definition file that references generic_action names. The planning engine resolves those to AWS executor modules via the catalog. Eight new executor files handle the real boto3 calls. A DB migration adds the new enum values, and the frontend gets the type labels and outcome templates.

**Tech Stack:** Python/FastAPI, boto3, SQLAlchemy/Alembic, React/TypeScript

---

## File Map

**New files:**
- `backend/alembic/versions/014_add_ec2_change_types.py`
- `backend/app/connectors/change_type_definitions/ec2_stop.json`
- `backend/app/connectors/change_type_definitions/ec2_start.json`
- `backend/app/connectors/change_type_definitions/ec2_reboot.json`
- `backend/app/connectors/change_type_definitions/ec2_stop_start.json`
- `backend/app/connectors/change_type_definitions/ec2_launch.json`
- `backend/app/connectors/change_type_definitions/ec2_terminate.json`
- `backend/app/connectors/executors/aws/capture_instance_state.py`
- `backend/app/connectors/executors/aws/stop_instance.py`
- `backend/app/connectors/executors/aws/start_instance.py`
- `backend/app/connectors/executors/aws/reboot_instance.py`
- `backend/app/connectors/executors/aws/wait_instance_state.py`
- `backend/app/connectors/executors/aws/resolve_launch_config.py`
- `backend/app/connectors/executors/aws/launch_instance.py`
- `backend/app/connectors/executors/aws/terminate_instance.py`
- `backend/app/tests/test_ec2_change_types.py`

**Modified files:**
- `backend/app/models/change_request.py` — add 6 ChangeType values
- `backend/app/connectors/catalog/aws.json` — add 8 action entries
- `backend/app/services/planning_engine.py` — add resolver entries + rollback strategies
- `frontend/src/types/api.ts` — add 6 ChangeType values
- `frontend/src/pages/CreateChangeRequest.tsx` — add 6 CHANGE_TYPE_META entries

---

### Task 1: DB Migration + ChangeType Enum

**Files:**
- Create: `backend/alembic/versions/014_add_ec2_change_types.py`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Write the migration file**

```python
# backend/alembic/versions/014_add_ec2_change_types.py
"""add ec2 instance lifecycle change types

Revision ID: 014
Revises: 013
Create Date: 2026-05-02
"""
from alembic import op

revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['ec2_stop', 'ec2_start', 'ec2_reboot', 'ec2_stop_start', 'ec2_launch', 'ec2_terminate']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
```

- [ ] **Step 2: Add enum values to the model**

In `backend/app/models/change_request.py`, add 6 values to `ChangeType` immediately after `microsegmentation_policy`:

```python
class ChangeType(str, enum.Enum):
    dns_update = "dns_update"
    snapshot_asset = "snapshot_asset"
    security_group_update = "security_group_update"
    key_rotation = "key_rotation"
    telemetry_agent_deploy = "telemetry_agent_deploy"
    remote_command = "remote_command"
    microsegmentation_policy = "microsegmentation_policy"
    ec2_stop = "ec2_stop"
    ec2_start = "ec2_start"
    ec2_reboot = "ec2_reboot"
    ec2_stop_start = "ec2_stop_start"
    ec2_launch = "ec2_launch"
    ec2_terminate = "ec2_terminate"
```

- [ ] **Step 3: Run the migration**

```bash
docker compose exec backend alembic upgrade 014
```
Expected: `Running upgrade 013 -> 014`

- [ ] **Step 4: Verify the model imports cleanly**

```bash
docker compose exec backend python -c "from app.models.change_request import ChangeType; print([c.value for c in ChangeType])"
```
Expected: list ending with `'ec2_stop', 'ec2_start', 'ec2_reboot', 'ec2_stop_start', 'ec2_launch', 'ec2_terminate'`

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/014_add_ec2_change_types.py backend/app/models/change_request.py
git commit -m "feat: add ec2_stop/start/reboot/stop_start/launch/terminate to ChangeType enum and migration 014"
```

---

### Task 2: Change Type Definition JSON Files

**Files:**
- Create: `backend/app/connectors/change_type_definitions/ec2_stop.json`
- Create: `backend/app/connectors/change_type_definitions/ec2_start.json`
- Create: `backend/app/connectors/change_type_definitions/ec2_reboot.json`
- Create: `backend/app/connectors/change_type_definitions/ec2_stop_start.json`
- Create: `backend/app/connectors/change_type_definitions/ec2_launch.json`
- Create: `backend/app/connectors/change_type_definitions/ec2_terminate.json`

- [ ] **Step 1: Create ec2_stop.json**

```json
{
  "change_type": "ec2_stop",
  "display_name": "Stop EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture",  "required": true},
    {"generic_action": "create_snapshot",        "purpose": "preflight_capture",  "required": true},
    {"generic_action": "stop_instance",          "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 2: Create ec2_start.json**

```json
{
  "change_type": "ec2_start",
  "display_name": "Start EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "start_instance",         "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Create ec2_reboot.json**

```json
{
  "change_type": "ec2_reboot",
  "display_name": "Reboot EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "reboot_instance",        "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 4: Create ec2_stop_start.json**

```json
{
  "change_type": "ec2_stop_start",
  "display_name": "Restart EC2 Instance (Stop/Start)",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "stop_instance",          "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true},
    {"generic_action": "start_instance",         "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 5: Create ec2_launch.json**

```json
{
  "change_type": "ec2_launch",
  "display_name": "Launch EC2 Instance",
  "steps": [
    {"generic_action": "resolve_launch_config", "purpose": "preflight_validate", "required": true},
    {"generic_action": "launch_instance",       "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",   "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Create ec2_terminate.json**

```json
{
  "change_type": "ec2_terminate",
  "display_name": "Terminate EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "create_snapshot",        "purpose": "preflight_capture", "required": true},
    {"generic_action": "terminate_instance",     "purpose": "execute",           "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/change_type_definitions/
git commit -m "feat: add ec2 change type definition JSON files (stop, start, reboot, stop_start, launch, terminate)"
```

---

### Task 3: AWS Executor Files — Instance State Operations

**Files:**
- Create: `backend/app/connectors/executors/aws/capture_instance_state.py`
- Create: `backend/app/connectors/executors/aws/stop_instance.py`
- Create: `backend/app/connectors/executors/aws/start_instance.py`
- Create: `backend/app/connectors/executors/aws/reboot_instance.py`
- Test: `backend/app/tests/test_ec2_change_types.py`

- [ ] **Step 1: Write failing tests for the four executors**

```python
# backend/app/tests/test_ec2_change_types.py
import pytest


def _mock_connector(creds=None):
    class C:
        credentials = creds or {}
    return C()


@pytest.mark.asyncio
async def test_capture_instance_state_mock():
    from app.connectors.executors.aws.capture_instance_state import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "capture_instance_state"
    assert "instance_id" in result
    assert "state" in result


@pytest.mark.asyncio
async def test_stop_instance_mock():
    from app.connectors.executors.aws.stop_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "stop_instance"
    assert result["instance_id"] == "i-abc123"


@pytest.mark.asyncio
async def test_stop_instance_rollback_calls_start():
    from app.connectors.executors.aws.stop_instance import rollback
    result = await rollback({"instance_id": "i-abc123"}, {}, _mock_connector())
    assert result["action"] == "start_instance"


@pytest.mark.asyncio
async def test_start_instance_mock():
    from app.connectors.executors.aws.start_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "start_instance"
    assert result["instance_id"] == "i-abc123"


@pytest.mark.asyncio
async def test_start_instance_rollback_calls_stop():
    from app.connectors.executors.aws.start_instance import rollback
    result = await rollback({"instance_id": "i-abc123"}, {}, _mock_connector())
    assert result["action"] == "stop_instance"


@pytest.mark.asyncio
async def test_reboot_instance_mock():
    from app.connectors.executors.aws.reboot_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "reboot_instance"
    assert result["rebooted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py -v 2>&1 | tail -15
```
Expected: `ModuleNotFoundError` or `ImportError` for each executor.

- [ ] **Step 3: Create capture_instance_state.py**

```python
# backend/app/connectors/executors/aws/capture_instance_state.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {
            "action": "capture_instance_state",
            "instance_id": instance_id or "i-mock000000000000",
            "state": "running",
            "public_ip": "1.2.3.4",
            "private_ip": "10.0.0.1",
            "ami_id": "ami-0abcdef1234567890",
            "instance_type": "t2.micro",
            "security_groups": ["sg-mock000000000000"],
            "subnet_id": "subnet-mock0000000000",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[instance_id]))
    reservations = resp.get('Reservations', [])
    if not reservations:
        return {"action": "capture_instance_state", "error": f"Instance {instance_id} not found"}
    inst = reservations[0]['Instances'][0]
    ni = inst.get('NetworkInterfaces', [{}])[0]
    return {
        "action": "capture_instance_state",
        "instance_id": inst['InstanceId'],
        "state": inst['State']['Name'],
        "public_ip": inst.get('PublicIpAddress'),
        "private_ip": inst.get('PrivateIpAddress'),
        "ami_id": inst.get('ImageId'),
        "instance_type": inst.get('InstanceType'),
        "security_groups": [sg['GroupId'] for sg in inst.get('SecurityGroups', [])],
        "subnet_id": inst.get('SubnetId'),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture has no rollback"}
```

- [ ] **Step 4: Create stop_instance.py**

```python
# backend/app/connectors/executors/aws/stop_instance.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "stop_instance", "instance_id": instance_id, "previous_state": "running", "current_state": "stopping"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
    change = resp['StoppingInstances'][0]
    return {
        "action": "stop_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": change['CurrentState']['Name'],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.start_instance import execute as start
    return await start(parameters, [], connector)
```

- [ ] **Step 5: Create start_instance.py**

```python
# backend/app/connectors/executors/aws/start_instance.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "start_instance", "instance_id": instance_id, "previous_state": "stopped", "current_state": "pending"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
    change = resp['StartingInstances'][0]
    return {
        "action": "start_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": change['CurrentState']['Name'],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.stop_instance import execute as stop
    return await stop(parameters, [], connector)
```

- [ ] **Step 6: Create reboot_instance.py**

```python
# backend/app/connectors/executors/aws/reboot_instance.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "reboot_instance", "instance_id": instance_id, "rebooted": True}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.reboot_instances(InstanceIds=[instance_id]))
    return {"action": "reboot_instance", "instance_id": instance_id, "rebooted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot is self-contained, no rollback needed"}
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py -v 2>&1 | tail -15
```
Expected: 6 tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/aws/capture_instance_state.py \
        backend/app/connectors/executors/aws/stop_instance.py \
        backend/app/connectors/executors/aws/start_instance.py \
        backend/app/connectors/executors/aws/reboot_instance.py \
        backend/app/tests/test_ec2_change_types.py
git commit -m "feat: add AWS executors for capture_instance_state, stop_instance, start_instance, reboot_instance"
```

---

### Task 4: AWS Executor Files — Wait, Launch, Terminate

**Files:**
- Create: `backend/app/connectors/executors/aws/wait_instance_state.py`
- Create: `backend/app/connectors/executors/aws/resolve_launch_config.py`
- Create: `backend/app/connectors/executors/aws/launch_instance.py`
- Create: `backend/app/connectors/executors/aws/terminate_instance.py`
- Modify: `backend/app/tests/test_ec2_change_types.py`

- [ ] **Step 1: Add tests for the four new executors**

Append to `backend/app/tests/test_ec2_change_types.py`:

```python
@pytest.mark.asyncio
async def test_wait_instance_state_mock_returns_target():
    from app.connectors.executors.aws.wait_instance_state import execute
    result = await execute({"instance_id": "i-abc123", "target_state": "running"}, [], _mock_connector())
    assert result["action"] == "wait_instance_state"
    assert result["reached_state"] == "running"


@pytest.mark.asyncio
async def test_resolve_launch_config_quick_mode():
    from app.connectors.executors.aws.resolve_launch_config import execute
    result = await execute({"mode": "quick", "name": "my-server", "os": "amazon_linux"}, [], _mock_connector())
    assert result["action"] == "resolve_launch_config"
    assert result["instance_type"] == "t2.micro"
    assert "ami_id" in result
    assert result["name"] == "my-server"


@pytest.mark.asyncio
async def test_resolve_launch_config_spec_mode():
    from app.connectors.executors.aws.resolve_launch_config import execute
    params = {
        "mode": "spec",
        "name": "spec-server",
        "ami_id": "ami-0abc123",
        "instance_type": "t3.small",
        "subnet_id": "subnet-abc",
        "security_group_ids": ["sg-abc"],
    }
    result = await execute(params, [], _mock_connector())
    assert result["ami_id"] == "ami-0abc123"
    assert result["instance_type"] == "t3.small"


@pytest.mark.asyncio
async def test_launch_instance_mock():
    from app.connectors.executors.aws.launch_instance import execute
    params = {"ami_id": "ami-0abc", "instance_type": "t2.micro", "subnet_id": "subnet-0", "security_group_ids": ["sg-0"], "name": "test"}
    result = await execute(params, [], _mock_connector())
    assert result["action"] == "launch_instance"
    assert result["instance_id"].startswith("i-")


@pytest.mark.asyncio
async def test_launch_instance_rollback_terminates():
    from app.connectors.executors.aws.launch_instance import rollback
    result = await rollback({}, {"instance_id": "i-abc123"}, _mock_connector())
    assert result["action"] == "terminate_instance"


@pytest.mark.asyncio
async def test_terminate_instance_blocked_without_confirm():
    from app.connectors.executors.aws.terminate_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert "error" in result


@pytest.mark.asyncio
async def test_terminate_instance_mock_with_confirm():
    from app.connectors.executors.aws.terminate_instance import execute
    result = await execute({"instance_id": "i-abc123", "confirm_terminate": True}, [], _mock_connector())
    assert result["action"] == "terminate_instance"
    assert result["instance_id"] == "i-abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py -v 2>&1 | tail -15
```
Expected: new tests fail with `ModuleNotFoundError`.

- [ ] **Step 3: Create wait_instance_state.py**

```python
# backend/app/connectors/executors/aws/wait_instance_state.py
import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    target_state = parameters.get('target_state', 'running')
    if not creds:
        return {"action": "wait_instance_state", "instance_id": instance_id, "reached_state": target_state, "elapsed_seconds": 0}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    start = time.time()
    for attempt in range(20):
        resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[instance_id]))
        reservations = resp.get('Reservations', [])
        if reservations:
            state = reservations[0]['Instances'][0]['State']['Name']
            if state == target_state:
                return {
                    "action": "wait_instance_state",
                    "instance_id": instance_id,
                    "reached_state": state,
                    "elapsed_seconds": int(time.time() - start),
                }
        await asyncio.sleep(15)
    raise TimeoutError(f"Instance {instance_id} did not reach state '{target_state}' within 5 minutes")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait step has no rollback"}
```

- [ ] **Step 4: Create resolve_launch_config.py**

```python
# backend/app/connectors/executors/aws/resolve_launch_config.py
import asyncio


_QUICK_AMI_FILTERS = {
    "amazon_linux": [
        {"Name": "name", "Values": ["al2023-ami-*-x86_64"]},
        {"Name": "owner-alias", "Values": ["amazon"]},
        {"Name": "state", "Values": ["available"]},
    ],
    "ubuntu": [
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
        {"Name": "owner-alias", "Values": ["aws-marketplace"]},
        {"Name": "state", "Values": ["available"]},
    ],
}

_MOCK_AMIS = {
    "amazon_linux": "ami-0abcdef1234567890",
    "ubuntu": "ami-0fedcba9876543210",
}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    mode = parameters.get('mode', 'quick')

    if mode == 'spec':
        required = ['ami_id', 'instance_type', 'subnet_id', 'security_group_ids', 'name']
        missing = [f for f in required if not parameters.get(f)]
        if missing:
            return {"action": "resolve_launch_config", "error": f"Missing required spec fields: {missing}"}
        return {
            "action": "resolve_launch_config",
            "mode": "spec",
            "ami_id": parameters['ami_id'],
            "instance_type": parameters['instance_type'],
            "subnet_id": parameters['subnet_id'],
            "security_group_ids": parameters['security_group_ids'],
            "name": parameters['name'],
        }

    if mode == 'clone':
        source_id = parameters.get('source_instance_id')
        if not source_id:
            return {"action": "resolve_launch_config", "error": "clone mode requires source_instance_id"}
        if not creds:
            return {
                "action": "resolve_launch_config",
                "mode": "clone",
                "ami_id": "ami-0abcdef1234567890",
                "instance_type": "t2.micro",
                "subnet_id": "subnet-mock0000000000",
                "security_group_ids": ["sg-mock000000000000"],
                "name": parameters.get('name', f"clone-of-{source_id}"),
            }
        from ._client import get_ec2_client
        ec2 = get_ec2_client(creds)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[source_id]))
        if not resp.get('Reservations'):
            return {"action": "resolve_launch_config", "error": f"Source instance {source_id} not found"}
        inst = resp['Reservations'][0]['Instances'][0]
        return {
            "action": "resolve_launch_config",
            "mode": "clone",
            "ami_id": inst['ImageId'],
            "instance_type": inst['InstanceType'],
            "subnet_id": inst['SubnetId'],
            "security_group_ids": [sg['GroupId'] for sg in inst.get('SecurityGroups', [])],
            "name": parameters.get('name', f"clone-of-{source_id}"),
        }

    # quick mode
    os_family = parameters.get('os', 'amazon_linux')
    name = parameters.get('name', 'nexplane-instance')
    if not creds:
        return {
            "action": "resolve_launch_config",
            "mode": "quick",
            "ami_id": _MOCK_AMIS.get(os_family, _MOCK_AMIS["amazon_linux"]),
            "instance_type": "t2.micro",
            "subnet_id": "subnet-mock0000000000",
            "security_group_ids": ["sg-mock000000000000"],
            "name": name,
        }
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    filters = _QUICK_AMI_FILTERS.get(os_family, _QUICK_AMI_FILTERS["amazon_linux"])
    imgs = await loop.run_in_executor(None, lambda: ec2.describe_images(Filters=filters))
    images = sorted(imgs.get('Images', []), key=lambda i: i['CreationDate'], reverse=True)
    ami_id = images[0]['ImageId'] if images else _MOCK_AMIS.get(os_family, _MOCK_AMIS["amazon_linux"])
    vpcs = await loop.run_in_executor(None, lambda: ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]))
    vpc_id = vpcs['Vpcs'][0]['VpcId'] if vpcs.get('Vpcs') else None
    subnets = await loop.run_in_executor(None, lambda: ec2.describe_subnets(Filters=[{"Name": "vpcId", "Values": [vpc_id]}])) if vpc_id else {"Subnets": []}
    subnet_id = subnets['Subnets'][0]['SubnetId'] if subnets.get('Subnets') else 'subnet-default'
    sgs = await loop.run_in_executor(None, lambda: ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": ["default"]}])) if vpc_id else {"SecurityGroups": []}
    sg_ids = [sg['GroupId'] for sg in sgs.get('SecurityGroups', [])] or ['sg-default']
    return {
        "action": "resolve_launch_config",
        "mode": "quick",
        "ami_id": ami_id,
        "instance_type": "t2.micro",
        "subnet_id": subnet_id,
        "security_group_ids": sg_ids,
        "name": name,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "resolve_launch_config has no rollback"}
```

- [ ] **Step 5: Create launch_instance.py**

```python
# backend/app/connectors/executors/aws/launch_instance.py
import asyncio
import random
import string


def _mock_instance_id():
    return "i-" + "".join(random.choices(string.hexdigits[:16], k=17))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    ami_id = parameters.get('ami_id', '')
    instance_type = parameters.get('instance_type', 't2.micro')
    subnet_id = parameters.get('subnet_id', '')
    security_group_ids = parameters.get('security_group_ids', [])
    name = parameters.get('name', 'nexplane-instance')
    if not creds:
        mock_id = _mock_instance_id()
        return {"action": "launch_instance", "instance_id": mock_id, "state": "pending", "private_ip": "10.0.1.100"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        SubnetId=subnet_id,
        SecurityGroupIds=security_group_ids,
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "nexplane"}],
        }],
    ))
    inst = resp['Instances'][0]
    return {
        "action": "launch_instance",
        "instance_id": inst['InstanceId'],
        "state": inst['State']['Name'],
        "private_ip": inst.get('PrivateIpAddress'),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_id = execution_result.get('instance_id')
    if not instance_id:
        return {"rolled_back": False, "reason": "no instance_id in execution result"}
    from app.connectors.executors.aws.terminate_instance import execute as terminate
    return await terminate({"instance_id": instance_id, "confirm_terminate": True}, [], connector)
```

- [ ] **Step 6: Create terminate_instance.py**

```python
# backend/app/connectors/executors/aws/terminate_instance.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not parameters.get('confirm_terminate'):
        return {"action": "terminate_instance", "error": "confirm_terminate must be true — termination is irreversible"}
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "terminate_instance", "instance_id": instance_id, "previous_state": "running", "current_state": "shutting-down"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.terminate_instances(InstanceIds=[instance_id]))
    change = resp['TerminatingInstances'][0]
    return {
        "action": "terminate_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": change['CurrentState']['Name'],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "termination is irreversible — restore from pre-terminate snapshot"}
```

- [ ] **Step 7: Run all tests**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py -v 2>&1 | tail -20
```
Expected: 14 tests pass, 0 failures.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/aws/wait_instance_state.py \
        backend/app/connectors/executors/aws/resolve_launch_config.py \
        backend/app/connectors/executors/aws/launch_instance.py \
        backend/app/connectors/executors/aws/terminate_instance.py \
        backend/app/tests/test_ec2_change_types.py
git commit -m "feat: add AWS executors for wait_instance_state, resolve_launch_config, launch_instance, terminate_instance"
```

---

### Task 5: AWS Catalog Entries

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`

- [ ] **Step 1: Add the 8 new action entries to the aws.json actions array**

Open `backend/app/connectors/catalog/aws.json` and append these 8 entries to the `"actions"` array (before the closing `]`):

```json
{
  "action_id": "capture_instance_state",
  "generic_action": "capture_instance_state",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Capture Instance State",
  "description": "Record current EC2 instance state, IPs, and config for rollback reference",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.capture_instance_state",
  "estimated_duration_seconds": 5
},
{
  "action_id": "stop_instance",
  "generic_action": "stop_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Stop EC2 Instance",
  "description": "Gracefully stop a running EC2 instance. Rollback: start the instance.",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.stop_instance",
  "estimated_duration_seconds": 30,
  "rollback_action": "start_instance"
},
{
  "action_id": "start_instance",
  "generic_action": "start_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Start EC2 Instance",
  "description": "Start a stopped EC2 instance. Rollback: stop the instance.",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.start_instance",
  "estimated_duration_seconds": 30,
  "rollback_action": "stop_instance"
},
{
  "action_id": "reboot_instance",
  "generic_action": "reboot_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Reboot EC2 Instance",
  "description": "Soft reboot. Instance stays on the same host and keeps its IP.",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true, "description": "EC2 instance ID"}
  ],
  "executor": "aws.reboot_instance",
  "estimated_duration_seconds": 60
},
{
  "action_id": "wait_instance_state",
  "generic_action": "wait_instance_state",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Wait for Instance State",
  "description": "Poll until EC2 instance reaches the target state (running or stopped).",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true},
    {"name": "target_state", "type": "string", "required": true, "description": "running or stopped"}
  ],
  "executor": "aws.wait_instance_state",
  "estimated_duration_seconds": 120
},
{
  "action_id": "resolve_launch_config",
  "generic_action": "resolve_launch_config",
  "action_type": "change",
  "execution_tier": 1,
  "display_name": "Resolve Launch Configuration",
  "description": "Build EC2 launch parameters from mode: quick (free-tier defaults), clone (copy source instance), or spec (operator-provided).",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "mode", "type": "string", "required": true, "description": "quick, clone, or spec"},
    {"name": "name", "type": "string", "required": true},
    {"name": "os", "type": "string", "required": false, "description": "amazon_linux or ubuntu (quick mode only)"},
    {"name": "source_instance_id", "type": "string", "required": false, "description": "EC2 instance to clone (clone mode only)"},
    {"name": "ami_id", "type": "string", "required": false},
    {"name": "instance_type", "type": "string", "required": false},
    {"name": "subnet_id", "type": "string", "required": false},
    {"name": "security_group_ids", "type": "array", "required": false}
  ],
  "executor": "aws.resolve_launch_config",
  "estimated_duration_seconds": 10
},
{
  "action_id": "launch_instance",
  "generic_action": "launch_instance",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Launch EC2 Instance",
  "description": "Launch a new EC2 instance using resolved config. Rollback: terminate the launched instance.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "ami_id", "type": "string", "required": true},
    {"name": "instance_type", "type": "string", "required": true},
    {"name": "subnet_id", "type": "string", "required": true},
    {"name": "security_group_ids", "type": "array", "required": true},
    {"name": "name", "type": "string", "required": true}
  ],
  "executor": "aws.launch_instance",
  "estimated_duration_seconds": 30,
  "rollback_action": "terminate_instance",
  "blast_radius_hint": "new_resource"
},
{
  "action_id": "terminate_instance",
  "generic_action": "terminate_instance",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Terminate EC2 Instance",
  "description": "Permanently terminate an EC2 instance. Requires confirm_terminate=true. Irreversible.",
  "applicable_asset_types": ["server", "cloud_account"],
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true},
    {"name": "confirm_terminate", "type": "boolean", "required": true}
  ],
  "executor": "aws.terminate_instance",
  "estimated_duration_seconds": 30,
  "blast_radius_hint": "destructive"
}
```

- [ ] **Step 2: Verify the catalog loads cleanly**

```bash
docker compose exec backend python -m pytest app/tests/test_catalog_service.py -v 2>&1 | tail -10
```
Expected: all catalog tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/aws.json
git commit -m "feat: add 8 new EC2 action entries to AWS catalog (capture_instance_state, stop/start/reboot/wait/resolve_launch_config/launch/terminate)"
```

---

### Task 6: Planning Engine — Resolvers and Rollback Strategies

**Files:**
- Modify: `backend/app/services/planning_engine.py`
- Test: `backend/app/tests/test_ec2_change_types.py`

- [ ] **Step 1: Add planning engine tests**

Append to `backend/app/tests/test_ec2_change_types.py`:

```python
import pathlib
import uuid
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module(module):
    init_catalog_service(CATALOG_DIR)


def _make_server_asset():
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), name="web-server-1",
        asset_type=AssetType.server, environment=Environment.prod,
        criticality=Criticality.high, asset_metadata={},
    )


def _make_cr(change_type, desired):
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=change_type,
        target_asset_ids=[], desired_outcome=desired, status=ChangeRequestStatus.draft,
    )
    return cr, [_make_server_asset()]


def test_ec2_stop_generates_four_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_stop, {"instance_id": "i-abc123", "snapshot_tag": "pre-stop"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 4
    assert plan.generated_steps[0]["generic_action"] == "capture_instance_state"
    assert plan.generated_steps[2]["generic_action"] == "stop_instance"
    assert plan.generated_steps[3]["generic_action"] == "wait_instance_state"


def test_ec2_start_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_start, {"instance_id": "i-abc123"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[1]["generic_action"] == "start_instance"


def test_ec2_stop_start_generates_five_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_stop_start, {"instance_id": "i-abc123"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 5
    actions = [s["generic_action"] for s in plan.generated_steps]
    assert actions == ["capture_instance_state", "stop_instance", "wait_instance_state", "start_instance", "wait_instance_state"]


def test_ec2_launch_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_launch, {"mode": "quick", "name": "new-server", "os": "amazon_linux"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[0]["generic_action"] == "resolve_launch_config"
    assert plan.generated_steps[1]["generic_action"] == "launch_instance"


def test_ec2_launch_rollback_strategy_is_automatic():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_launch, {"mode": "quick", "name": "test"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert plan.rollback_plan["automatic"] is True


def test_ec2_terminate_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_terminate, {"instance_id": "i-abc123", "confirm_terminate": True, "snapshot_tag": "pre-terminate"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[2]["generic_action"] == "terminate_instance"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py::test_ec2_stop_generates_four_steps -v 2>&1 | tail -10
```
Expected: FAIL — `KeyError` on missing change type def or resolver.

- [ ] **Step 3: Add resolvers to planning_engine.py**

In `backend/app/services/planning_engine.py`, inside `_resolve_parameters`, add these entries to the `resolvers` dict (after the existing `"validate_staged"` entry):

```python
        "capture_instance_state": {"instance_id": desired.get("instance_id", "")},
        "stop_instance":          {"instance_id": desired.get("instance_id", "")},
        "start_instance":         {"instance_id": desired.get("instance_id", "")},
        "reboot_instance":        {"instance_id": desired.get("instance_id", "")},
        "wait_instance_state":    {"instance_id": desired.get("instance_id", ""), "target_state": desired.get("target_state", "running")},
        "resolve_launch_config":  {
            "mode": desired.get("mode", "quick"),
            "name": desired.get("name", "nexplane-instance"),
            "os": desired.get("os", "amazon_linux"),
            "source_instance_id": desired.get("source_instance_id"),
            "ami_id": desired.get("ami_id"),
            "instance_type": desired.get("instance_type"),
            "subnet_id": desired.get("subnet_id"),
            "security_group_ids": desired.get("security_group_ids", []),
        },
        "launch_instance":        {
            "ami_id": desired.get("ami_id", ""),
            "instance_type": desired.get("instance_type", "t2.micro"),
            "subnet_id": desired.get("subnet_id", ""),
            "security_group_ids": desired.get("security_group_ids", []),
            "name": desired.get("name", "nexplane-instance"),
        },
        "terminate_instance":     {"instance_id": desired.get("instance_id", ""), "confirm_terminate": desired.get("confirm_terminate", False)},
```

- [ ] **Step 4: Add rollback strategies to _generate_rollback_plan**

In `backend/app/services/planning_engine.py`, inside `_generate_rollback_plan`, add 6 entries to the `strategies` dict:

```python
        ChangeType.ec2_stop:       ("start_instance",     True),
        ChangeType.ec2_start:      ("stop_instance",      True),
        ChangeType.ec2_reboot:     ("none",               False),
        ChangeType.ec2_stop_start: ("stop_if_running",    True),
        ChangeType.ec2_launch:     ("terminate_instance", True),
        ChangeType.ec2_terminate:  ("manual",             False),
```

- [ ] **Step 5: Run all planning engine tests**

```bash
docker compose exec backend python -m pytest app/tests/test_ec2_change_types.py app/tests/test_planning_engine.py -v 2>&1 | tail -20
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/planning_engine.py backend/app/tests/test_ec2_change_types.py
git commit -m "feat: add EC2 action resolvers and rollback strategies to planning engine"
```

---

### Task 7: Frontend — Types and Change Type Metadata

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Add ChangeType values to api.ts**

In `frontend/src/types/api.ts`, replace the `ChangeType` union (currently ends at `"microsegmentation_policy"`) with:

```typescript
export type ChangeType =
  | "dns_update"
  | "snapshot_asset"
  | "security_group_update"
  | "key_rotation"
  | "telemetry_agent_deploy"
  | "remote_command"
  | "microsegmentation_policy"
  | "ec2_stop"
  | "ec2_start"
  | "ec2_reboot"
  | "ec2_stop_start"
  | "ec2_launch"
  | "ec2_terminate";
```

- [ ] **Step 2: Add CHANGE_TYPE_META entries in CreateChangeRequest.tsx**

In `frontend/src/pages/CreateChangeRequest.tsx`, add the following 6 entries to `CHANGE_TYPE_META` (after the `microsegmentation_policy` entry):

```typescript
  ec2_stop: {
    label: "Stop EC2 Instance",
    description: "Gracefully stop a running instance. Takes an EBS snapshot first as a safety net.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-stop-nexplane",
    }, null, 2),
  },
  ec2_start: {
    label: "Start EC2 Instance",
    description: "Start a stopped EC2 instance.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_reboot: {
    label: "Reboot EC2 Instance",
    description: "Soft reboot — stays on the same host, keeps its public IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_stop_start: {
    label: "Restart EC2 Instance",
    description: "Full power cycle (stop then start). Instance may get a new public IP if not using an Elastic IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_launch: {
    label: "Launch EC2 Instance",
    description: "Launch a new instance. Set mode to 'quick' (free-tier defaults), 'clone' (copy existing), or 'spec' (full parameters).",
    outcomeTemplate: JSON.stringify({
      mode: "quick",
      name: "my-new-instance",
      os: "amazon_linux",
    }, null, 2),
  },
  ec2_terminate: {
    label: "Terminate EC2 Instance",
    description: "Permanently terminate an instance. Takes a mandatory snapshot first. Irreversible.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-terminate-nexplane",
      confirm_terminate: true,
    }, null, 2),
  },
```

- [ ] **Step 3: Restart frontend and verify no TypeScript errors**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 8 seconds, then:

```bash
docker compose logs frontend --tail=5 2>&1 | grep -v warning
```
Expected: `VITE v6.4.2  ready` with no errors.

- [ ] **Step 4: Manually verify in the browser**

Open http://localhost:3000 → Create Change Request. Confirm the 6 new EC2 types appear in the grid with correct labels and that clicking each one populates the outcome JSON template correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add EC2 change types to frontend — ChangeType union and CHANGE_TYPE_META entries"
```

---

### Task 8: Full Test Run and Final Verification

- [ ] **Step 1: Run the full backend test suite**

```bash
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -10
```
Expected: all tests pass, 0 failures.

- [ ] **Step 2: Smoke test with real AWS (if credentials are configured)**

In the running app at http://localhost:3000:
1. Go to **Change Requests → Create**
2. Select **"Stop EC2 Instance"**
3. Enter a real `instance_id` from your AWS account in the outcome JSON
4. Select the AWS connector asset
5. Submit → verify a plan is generated with 4 steps

- [ ] **Step 3: Final commit**

```bash
git add -A
git status
# confirm nothing untracked
git commit -m "feat: complete AWS EC2 instance lifecycle change types (stop, start, reboot, stop/start, launch, terminate)" --allow-empty-if-only-existing
```

If there's nothing left to add, just verify everything is committed with `git status`.
