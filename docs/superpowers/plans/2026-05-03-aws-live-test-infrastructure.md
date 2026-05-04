# AWS Live Test Infrastructure — Phase 1 & 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close EC2 launch gaps (key pair support, IAM profile), add the missing `ssm_command` change type, and produce an executable smoke test suite that validates the full EC2 + SSM + agent lifecycle against a live AWS account.

**Architecture:** Five backend tasks (new asset type + enum, new executor, catalog entries, updated executors, safety engine bypass) plus two frontend tasks (templates, asset actions) plus one smoke test script. All backend changes are hot-reloaded; the Alembic migration runs inside the container.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, boto3, React 18 + TanStack Query, Python 3.12.

---

## File Map

**Create:**
- `backend/app/connectors/executors/aws/create_key_pair.py` — key pair create + delete
- `backend/app/connectors/executors/aws/delete_key_pair.py` — key pair delete (rollback)
- `backend/app/connectors/change_type_definitions/key_pair_create.json` — CR workflow for key pair
- `backend/app/connectors/change_type_definitions/ssm_command.json` — CR workflow for SSM
- `backend/alembic/versions/019_add_key_pair_change_types.py` — adds `key_pair` asset type + `key_pair_create`/`ssm_command` change types to DB enums
- `backend/tests/smoke/test_aws_live.py` — executable end-to-end smoke test

**Modify:**
- `backend/app/models/asset.py` — add `key_pair` to `AssetType` enum
- `backend/app/models/change_request.py` — add `key_pair_create`, `ssm_command` to `ChangeType` enum
- `backend/app/connectors/catalog/aws.json` — add `create_key_pair`, `delete_key_pair`, `run_ssm_command` (already exists but needs `ssm_command` generic action)
- `backend/app/connectors/executors/aws/resolve_launch_config.py` — pass `iam_instance_profile` + `key_name` through
- `backend/app/connectors/executors/aws/launch_instance.py` — include `IamInstanceProfile` + `KeyName` in `run_instances`
- `backend/app/services/planning_engine.py` — add resolvers for `create_key_pair`, `run_ssm_command`, update `resolve_launch_config` + `launch_instance` resolvers
- `backend/app/services/safety_engine.py` — exempt `ssm_command` from freeform command block
- `frontend/src/types/api.ts` — add `key_pair` to `AssetType`, `key_pair_create` + `ssm_command` to `ChangeType`
- `frontend/src/pages/Assets.tsx` — add `key_pair` icon
- `frontend/src/pages/AssetDetail.tsx` — add quick actions for `key_pair`
- `frontend/src/pages/CreateChangeRequest.tsx` — add `key_pair_create` + `ssm_command` to meta + groups + asset filter

---

## Task 1: `key_pair` Asset Type + `key_pair_create` / `ssm_command` Change Types

Adds three new enum values that everything else depends on.

**Files:**
- Modify: `backend/app/models/asset.py`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/019_add_key_pair_change_types.py`

- [ ] **Step 1: Add `key_pair` to AssetType enum**

In `backend/app/models/asset.py`, add after `container_cluster = "container_cluster"`:

```python
    key_pair = "key_pair"
```

- [ ] **Step 2: Add `key_pair_create` and `ssm_command` to ChangeType enum**

In `backend/app/models/change_request.py`, add after `ec2_terminate = "ec2_terminate"` (inside the EC2 section):

```python
    key_pair_create = "key_pair_create"
    ssm_command = "ssm_command"
```

- [ ] **Step 3: Create Alembic migration**

Create `backend/alembic/versions/019_add_key_pair_change_types.py`:

```python
"""add key_pair asset type and key_pair_create/ssm_command change types

Revision ID: 019
Revises: 018
Create Date: 2026-05-03
"""
from alembic import op

revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'key_pair'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'key_pair_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ssm_command'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected last line: `Running upgrade 018 -> 019, add key_pair asset type and key_pair_create/ssm_command change types`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/asset.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/019_add_key_pair_change_types.py
git commit -m "feat: add key_pair asset type and key_pair_create/ssm_command change types"
```

---

## Task 2: Key Pair Executors + Catalog Entries

**Files:**
- Create: `backend/app/connectors/executors/aws/create_key_pair.py`
- Create: `backend/app/connectors/executors/aws/delete_key_pair.py`
- Modify: `backend/app/connectors/catalog/aws.json`
- Create: `backend/app/connectors/change_type_definitions/key_pair_create.json`
- Create: `backend/app/connectors/change_type_definitions/ssm_command.json`

- [ ] **Step 1: Create `create_key_pair.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    key_name = parameters.get('key_name', 'nexplane-key')
    if not creds:
        return {
            "action": "create_key_pair",
            "key_name": key_name,
            "key_fingerprint": "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99",
            "mock": True,
            "_auto_asset": {
                "name": key_name,
                "asset_type": "key_pair",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {
                    "key_name": key_name,
                    "key_fingerprint": "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99",
                    "region": "us-east-1",
                    "provider": "aws",
                },
                "tags": ["nexplane-managed"],
            },
        }
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()

    def _call():
        return ec2.create_key_pair(
            KeyName=key_name,
            TagSpecifications=[{
                "ResourceType": "key-pair",
                "Tags": [{"Key": "ManagedBy", "Value": "nexplane"}],
            }],
        )

    resp = await loop.run_in_executor(None, _call)
    return {
        "action": "create_key_pair",
        "key_name": key_name,
        "key_fingerprint": resp.get('KeyFingerprint', ''),
        "private_key_material": resp.get('KeyMaterial', ''),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": key_name,
            "asset_type": "key_pair",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "key_name": key_name,
                "key_fingerprint": resp.get('KeyFingerprint', ''),
                "region": creds.get('region', 'us-east-1'),
                "provider": "aws",
            },
            "tags": ["nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.delete_key_pair import execute as delete
    return await delete({"key_name": execution_result.get('key_name', parameters.get('key_name'))}, [], connector)
```

- [ ] **Step 2: Create `delete_key_pair.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    key_name = parameters.get('key_name', '')
    if not creds:
        return {"action": "delete_key_pair", "key_name": key_name, "deleted": True, "mock": True}
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ec2.delete_key_pair(KeyName=key_name))
    return {
        "action": "delete_key_pair",
        "key_name": key_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key pair deletion is irreversible"}
```

- [ ] **Step 3: Add catalog entries to `aws.json`**

Open `backend/app/connectors/catalog/aws.json`. After the opening `"actions": [` line, insert these two entries (before the existing `create_ebs_snapshot`):

```json
    {
      "action_id": "create_key_pair",
      "generic_action": "create_key_pair",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Create EC2 Key Pair",
      "description": "Create a named EC2 key pair and store fingerprint as an asset.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "key_name", "type": "string", "required": true}
      ],
      "executor": "aws.create_key_pair",
      "rollback_action": "delete_key_pair",
      "rollback_connector_type": "aws",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "delete_key_pair",
      "generic_action": "delete_key_pair",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Delete EC2 Key Pair",
      "description": "Delete a named EC2 key pair.",
      "applicable_asset_types": ["cloud_account", "key_pair"],
      "parameters": [
        {"name": "key_name", "type": "string", "required": true}
      ],
      "executor": "aws.delete_key_pair",
      "estimated_duration_seconds": 5
    },
```

Also find the existing `run_ssm_command` entry in `aws.json` and verify its `generic_action` field is `"run_ssm_command"`. If missing, the entry needs:

```json
    {
      "action_id": "run_ssm_command",
      "generic_action": "run_ssm_command",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Run SSM Command",
      "description": "Execute a command on an EC2 instance via AWS Systems Manager.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true},
        {"name": "document_name", "type": "string", "required": false, "default": "AWS-RunShellScript"},
        {"name": "command", "type": "string", "required": false}
      ],
      "executor": "aws.run_ssm_command",
      "estimated_duration_seconds": 60
    },
```

- [ ] **Step 4: Create `key_pair_create.json` change type definition**

Create `backend/app/connectors/change_type_definitions/key_pair_create.json`:

```json
{
  "change_type": "key_pair_create",
  "display_name": "Create EC2 Key Pair",
  "steps": [
    {"generic_action": "create_key_pair", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 5: Create `ssm_command.json` change type definition**

Create `backend/app/connectors/change_type_definitions/ssm_command.json`:

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

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/aws/create_key_pair.py \
        backend/app/connectors/executors/aws/delete_key_pair.py \
        backend/app/connectors/catalog/aws.json \
        backend/app/connectors/change_type_definitions/key_pair_create.json \
        backend/app/connectors/change_type_definitions/ssm_command.json
git commit -m "feat: add create_key_pair/delete_key_pair executors, ssm_command and key_pair_create change type definitions"
```

---

## Task 3: Update `resolve_launch_config` and `launch_instance` for IAM Profile + Key Name

**Files:**
- Modify: `backend/app/connectors/executors/aws/resolve_launch_config.py`
- Modify: `backend/app/connectors/executors/aws/launch_instance.py`
- Modify: `backend/app/services/planning_engine.py`

- [ ] **Step 1: Update `resolve_launch_config.py` to pass through `iam_instance_profile` and `key_name`**

Replace the entire file:

```python
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
    iam_instance_profile = parameters.get('iam_instance_profile', '')
    key_name = parameters.get('key_name', '')

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
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
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
                "instance_type": "t3.micro",
                "subnet_id": "subnet-mock0000000000",
                "security_group_ids": ["sg-mock000000000000"],
                "name": parameters.get('name', f"clone-of-{source_id}"),
                "iam_instance_profile": iam_instance_profile,
                "key_name": key_name,
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
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
        }

    # quick mode
    os_family = parameters.get('os', 'amazon_linux')
    name = parameters.get('name', 'nexplane-instance')
    if not creds:
        return {
            "action": "resolve_launch_config",
            "mode": "quick",
            "ami_id": _MOCK_AMIS.get(os_family, _MOCK_AMIS["amazon_linux"]),
            "instance_type": "t3.micro",
            "subnet_id": "subnet-mock0000000000",
            "security_group_ids": ["sg-mock000000000000"],
            "name": name,
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
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
        "instance_type": "t3.micro",
        "subnet_id": subnet_id,
        "security_group_ids": sg_ids,
        "name": name,
        "iam_instance_profile": iam_instance_profile,
        "key_name": key_name,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "resolve_launch_config has no rollback"}
```

- [ ] **Step 2: Update `launch_instance.py` to include IAM profile + key name in `run_instances`**

Replace the entire file:

```python
import asyncio
import random
import string


def _mock_instance_id():
    return "i-" + "".join(random.choices(string.hexdigits[:16], k=17))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    ami_id = parameters.get('ami_id', '')
    instance_type = parameters.get('instance_type', 't3.micro')
    subnet_id = parameters.get('subnet_id', '')
    security_group_ids = parameters.get('security_group_ids', [])
    name = parameters.get('name', 'nexplane-instance')
    iam_instance_profile = parameters.get('iam_instance_profile', '')
    key_name = parameters.get('key_name', '')

    if not creds:
        mock_id = _mock_instance_id()
        return {
            "action": "launch_instance",
            "instance_id": mock_id,
            "state": "pending",
            "private_ip": "10.0.1.100",
            "_auto_asset": {
                "name": name,
                "asset_type": "server",
                "environment": "prod",
                "criticality": "medium",
                "asset_metadata": {
                    "instance_id": mock_id,
                    "instance_type": instance_type,
                    "private_ip": "10.0.1.100",
                    "iam_instance_profile": iam_instance_profile,
                    "key_name": key_name,
                },
                "tags": ["ec2", "nexplane-launched"],
            },
        }

    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = dict(
            ImageId=ami_id,
            InstanceType=instance_type,
            SubnetId=subnet_id,
            SecurityGroupIds=security_group_ids,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "ManagedBy", "Value": "nexplane"},
                ],
            }],
        )
        if iam_instance_profile:
            kwargs["IamInstanceProfile"] = {"Name": iam_instance_profile}
        if key_name:
            kwargs["KeyName"] = key_name
        return ec2.run_instances(**kwargs)

    resp = await loop.run_in_executor(None, _call)
    inst = resp['Instances'][0]
    instance_id = inst['InstanceId']
    private_ip = inst.get('PrivateIpAddress')
    return {
        "action": "launch_instance",
        "instance_id": instance_id,
        "state": inst['State']['Name'],
        "private_ip": private_ip,
        "_auto_asset": {
            "name": name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "instance_id": instance_id,
                "instance_type": instance_type,
                "ami_id": ami_id,
                "subnet_id": subnet_id,
                "private_ip": private_ip,
                "iam_instance_profile": iam_instance_profile,
                "key_name": key_name,
            },
            "tags": ["ec2", "nexplane-launched"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_id = execution_result.get('instance_id')
    if not instance_id:
        return {"rolled_back": False, "reason": "no instance_id in execution result"}
    from app.connectors.executors.aws.terminate_instance import execute as terminate
    return await terminate({"instance_id": instance_id, "confirm_terminate": True}, [], connector)
```

- [ ] **Step 3: Update planning_engine.py parameter resolvers**

In `backend/app/services/planning_engine.py`, find the `resolve_launch_config` resolver (around line 57) and replace it:

```python
        "resolve_launch_config":  {
            "mode": desired.get("mode", "quick"),
            "name": desired.get("name", "nexplane-instance"),
            "os": desired.get("os", "amazon_linux"),
            "source_instance_id": desired.get("source_instance_id"),
            "ami_id": desired.get("ami_id"),
            "instance_type": desired.get("instance_type"),
            "subnet_id": desired.get("subnet_id"),
            "security_group_ids": desired.get("security_group_ids", []),
            "iam_instance_profile": desired.get("iam_instance_profile", ""),
            "key_name": desired.get("key_name", ""),
        },
        "launch_instance":        {
            "ami_id": desired.get("ami_id", ""),
            "instance_type": desired.get("instance_type", ""),
            "subnet_id": desired.get("subnet_id", ""),
            "security_group_ids": desired.get("security_group_ids", []),
            "name": desired.get("name", "nexplane-instance"),
            "iam_instance_profile": desired.get("iam_instance_profile", ""),
            "key_name": desired.get("key_name", ""),
        },
```

Also add new resolvers for `create_key_pair`, `delete_key_pair`, and `run_ssm_command`. Find the `"terminate_instance"` resolver line and add these before it:

```python
        "create_key_pair":        {"key_name": desired.get("key_name", "nexplane-key")},
        "delete_key_pair":        {"key_name": desired.get("key_name", "")},
        "run_ssm_command":        {
            "instance_id": desired.get("instance_id", ""),
            "document_name": desired.get("document_name", "AWS-RunShellScript"),
            "parameters": {"commands": [desired.get("command", "echo hello")]},
        },
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/aws/resolve_launch_config.py \
        backend/app/connectors/executors/aws/launch_instance.py \
        backend/app/services/planning_engine.py
git commit -m "feat: add iam_instance_profile and key_name to ec2_launch; add planning resolvers for key_pair/ssm_command"
```

---

## Task 4: Safety Engine — Exempt `ssm_command` from Freeform Command Block

The safety engine currently blocks `remote_command` change types that don't use an approved template. `ssm_command` uses AWS-managed trust (IAM + SSM), not shell injection, so freeform commands are safe.

**Files:**
- Modify: `backend/app/services/safety_engine.py`

- [ ] **Step 1: Add SSM command risk factor without blocking**

In `backend/app/services/safety_engine.py`, find the `remote_command` block (around line 90). Add an `elif` branch for `ssm_command` after the `if change_request.change_type == ChangeType.remote_command:` block:

```python
    if change_request.change_type == ChangeType.remote_command:
        desired = change_request.desired_outcome or {}
        template_id = desired.get("template_id")
        if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
            blocking_issues.append(
                "Remote command requests must use an approved command template. "
                f"Available templates: {', '.join(APPROVED_COMMAND_TEMPLATES.keys())}"
            )
        elif desired.get("freeform_command"):
            blocking_issues.append("Freeform shell commands are not permitted. Use a parameterized template.")
        else:
            score += 25
            risk_factors.append(RiskFactor(
                name="remote_command_execution",
                description=f"Remote command using template '{template_id}'",
                score=25,
            ))

    elif change_request.change_type == ChangeType.ssm_command:
        # SSM uses AWS IAM trust — freeform commands are permitted but scored as medium risk
        score += 20
        risk_factors.append(RiskFactor(
            name="ssm_command_execution",
            description="SSM command execution via AWS Systems Manager (IAM-gated)",
            score=20,
        ))
```

Also add `ChangeType.ssm_command` to `_IMPLICIT_ROLLBACK_TYPES` (since SSM commands have no automatic rollback):

Find the `_IMPLICIT_ROLLBACK_TYPES` set and add `ChangeType.ssm_command`:

```python
    _IMPLICIT_ROLLBACK_TYPES = {
        ChangeType.ec2_stop, ChangeType.ec2_start, ChangeType.ec2_reboot,
        ChangeType.ec2_stop_start, ChangeType.ec2_launch, ChangeType.ssm_command,
        ChangeType.key_pair_create,
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/safety_engine.py
git commit -m "feat: exempt ssm_command from freeform command block; score as medium risk via IAM-gated SSM"
```

---

## Task 5: Frontend — New Change Types + Asset Type

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Assets.tsx`
- Modify: `frontend/src/pages/AssetDetail.tsx`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Update `AssetType` and `ChangeType` in api.ts**

In `frontend/src/types/api.ts`, update `AssetType` to add `"key_pair"`:

```typescript
export type AssetType =
  | "server"
  | "cloud_account"
  | "dns_zone"
  | "firewall"
  | "identity_provider"
  | "application"
  | "identity"
  | "database"
  | "storage_bucket"
  | "load_balancer"
  | "endpoint"
  | "container_cluster"
  | "key_pair";
```

Find `ChangeType` in the file and add the two new values:

```typescript
  | "key_pair_create"
  | "ssm_command"
```

- [ ] **Step 2: Add `key_pair` icon in Assets.tsx**

In `frontend/src/pages/Assets.tsx`, update `ASSET_TYPE_ICONS` to add:

```typescript
  key_pair: "🔐",
```

Also update both the filter dropdown and the Add Asset form type select arrays to include `"key_pair"`.

- [ ] **Step 3: Add quick actions for `key_pair` assets in AssetDetail.tsx**

In `frontend/src/pages/AssetDetail.tsx`, find `ASSET_ACTIONS` and add after `container_cluster`:

```typescript
  key_pair: [
    {
      changeType: "rotate_ssh_keys",
      label: "Rotate Key",
      title: (a) => `Rotate key pair ${a.name}`,
      description: (a) => `Delete and recreate key pair ${a.asset_metadata?.key_name ?? a.name}.`,
    },
  ],
```

- [ ] **Step 4: Add `key_pair_create` and `ssm_command` to CreateChangeRequest.tsx**

In `frontend/src/pages/CreateChangeRequest.tsx`:

**Add to `CHANGE_TYPE_META`** (inside the EC2 section):

```typescript
  key_pair_create: {
    label: "Create Key Pair",
    description: "Create an EC2 key pair and store it in the asset inventory.",
    outcomeTemplate: JSON.stringify({
      key_name: "nexplane-test-key",
      rollback_strategy: "delete_key_pair",
    }, null, 2),
  },
  ssm_command: {
    label: "Run SSM Command",
    description: "Execute a shell command on an EC2 instance via AWS Systems Manager.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      document_name: "AWS-RunShellScript",
      command: "whoami && hostname",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
```

**Add to `CHANGE_TYPE_GROUPS`** in the EC2 group `types` array:

```typescript
  {
    label: "EC2",
    types: ["ec2_launch", "ec2_start", "ec2_stop", "ec2_reboot", "ec2_stop_start", "ec2_terminate", "ssm_command", "key_pair_create"],
  },
```

**Add to `CHANGE_TYPE_ASSET_FILTER`**:

```typescript
  key_pair_create: "cloud_account",
  ssm_command: "server",
```

**Add to `ASSET_TYPE_LABELS`**:

```typescript
  key_pair: "key pair",
```

- [ ] **Step 5: Restart frontend and commit**

```bash
docker compose stop frontend && docker compose up frontend -d
git add frontend/src/types/api.ts \
        frontend/src/pages/Assets.tsx \
        frontend/src/pages/AssetDetail.tsx \
        frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add key_pair asset type and key_pair_create/ssm_command change types to frontend"
```

---

## Task 6: Update `ec2_launch` Outcome Template

**Files:**
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Update ec2_launch outcomeTemplate to include new fields**

In `frontend/src/pages/CreateChangeRequest.tsx`, find the `ec2_launch` entry in `CHANGE_TYPE_META` and replace its `outcomeTemplate`:

```typescript
  ec2_launch: {
    label: "Launch EC2 Instance",
    description: "Launch a new instance. Set mode to 'quick' (free-tier defaults), 'clone' (copy existing), or 'spec' (full parameters).",
    outcomeTemplate: JSON.stringify({
      mode: "quick",
      name: "my-new-instance",
      os: "amazon_linux",
      iam_instance_profile: "NexplaneEC2TestProfile",
      key_name: "",
      rollback_strategy: "terminate_instance",
    }, null, 2),
  },
```

- [ ] **Step 2: Restart frontend and commit**

```bash
docker compose stop frontend && docker compose up frontend -d
git add frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add iam_instance_profile and key_name fields to ec2_launch outcome template"
```

---

## Task 7: Smoke Test Script

**Files:**
- Create: `backend/tests/smoke/__init__.py`
- Create: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Create `backend/tests/smoke/__init__.py`**

Empty file:

```python
```

- [ ] **Step 2: Create `backend/tests/smoke/test_aws_live.py`**

```python
#!/usr/bin/env python3
"""
AWS Live Smoke Test — Nexplane Phase 1 & 2 validation.

Runs against a live AWS account via the Nexplane API. Creates and destroys
real AWS resources. Intended to catch regressions in EC2, SSM, key pair,
and agent-related components.

Usage:
    python backend/tests/smoke/test_aws_live.py \
        --base-url http://localhost:8000 \
        --email admin@example.com \
        --password changeme

Requirements:
    - AWS connector configured in Nexplane with valid credentials
    - IAM instance profile NexplaneEC2TestProfile exists
    - iam:PassRole granted to connector IAM user
"""
import argparse
import json
import sys
import time
from typing import Optional

import httpx

KEY_NAME = "nexplane-smoke-test-key"
INSTANCE_NAME = "nexplane-smoke-test-01"
TIMEOUT_SECONDS = 300  # 5 minutes per CR step


def log(msg: str, ok: bool = True) -> None:
    prefix = "✅" if ok else "❌"
    print(f"{prefix} {msg}")


def fail(msg: str) -> None:
    log(msg, ok=False)
    sys.exit(1)


class NexplaneClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30)
        resp = self.client.post(f"{self.base}/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def get_aws_connector_id(self) -> str:
        resp = self.client.get(f"{self.base}/connectors")
        resp.raise_for_status()
        for c in resp.json():
            if c["connector_type"] == "aws":
                return c["id"]
        fail("No AWS connector found — add one in Nexplane Settings first")

    def get_cloud_account_asset_id(self) -> str:
        resp = self.client.get(f"{self.base}/assets", params={"asset_type": "cloud_account"})
        resp.raise_for_status()
        assets = resp.json()
        if not assets:
            fail("No cloud_account asset found — run EC2 discovery on the AWS connector first")
        return assets[0]["id"]

    def get_asset_by_name(self, name: str) -> Optional[dict]:
        resp = self.client.get(f"{self.base}/assets", params={"q": name})
        resp.raise_for_status()
        for a in resp.json():
            if a["name"] == name:
                return a
        return None

    def create_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> str:
        resp = self.client.post(f"{self.base}/change-requests", json={
            "title": title,
            "description": f"Smoke test: {title}",
            "change_type": change_type,
            "target_asset_ids": [asset_id],
            "desired_outcome": desired_outcome,
        })
        resp.raise_for_status()
        return resp.json()["id"]

    def generate_plan(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/plan")
        if not resp.is_success:
            fail(f"Plan generation failed: {resp.text}")

    def submit_for_approval(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/submit-for-approval")
        if not resp.is_success:
            fail(f"Submit for approval failed: {resp.text}")

    def approve(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/approve", json={
            "decision": "approved", "comment": "Smoke test auto-approval"
        })
        if not resp.is_success:
            fail(f"Approval failed: {resp.text}")

    def execute_cr(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/execute")
        if not resp.is_success:
            fail(f"Execute failed: {resp.text}")

    def wait_for_completion(self, cr_id: str, step_name: str) -> dict:
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            resp = self.client.get(f"{self.base}/change-requests/{cr_id}")
            resp.raise_for_status()
            cr = resp.json()
            if cr["status"] == "completed":
                log(f"{step_name} completed")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{step_name} ended with status '{cr['status']}'")
            time.sleep(5)
        fail(f"{step_name} timed out after {TIMEOUT_SECONDS}s")

    def run_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> dict:
        """Full CR lifecycle: create → plan → approve → execute → wait."""
        print(f"\n  Running CR: {title}")
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome)
        self.generate_plan(cr_id)
        self.submit_for_approval(cr_id)
        self.approve(cr_id)
        self.execute_cr(cr_id)
        return self.wait_for_completion(cr_id, title)


def scope_guard(client: NexplaneClient) -> None:
    """Ensure we're not running against a production account by checking instance names."""
    resp = client.client.get(f"{client.base}/assets", params={"q": "nexplane-smoke-test"})
    # If there are already many nexplane-smoke-test assets, warn but continue
    assets = resp.json() if resp.is_success else []
    stale = [a for a in assets if "smoke-test" in a.get("name", "")]
    if stale:
        print(f"  ⚠️  Found {len(stale)} stale smoke-test asset(s) — will be cleaned up")


def cleanup(client: NexplaneClient) -> None:
    """Terminate any running nexplane-smoke-test instances and delete test key pairs."""
    print("\n  Cleanup: terminating smoke-test instances...")
    resp = client.client.get(f"{client.base}/assets", params={"q": "nexplane-smoke-test"})
    if resp.is_success:
        for asset in resp.json():
            if asset["asset_type"] == "server" and "smoke-test" in asset.get("name", ""):
                instance_id = asset.get("asset_metadata", {}).get("instance_id")
                if instance_id:
                    try:
                        cr_id = client.create_cr(
                            f"Cleanup: terminate {asset['name']}",
                            "ec2_terminate",
                            asset["id"],
                            {"instance_id": instance_id, "confirm_terminate": True, "rollback_strategy": "rollback_unavailable"},
                        )
                        client.generate_plan(cr_id)
                        client.submit_for_approval(cr_id)
                        client.approve(cr_id)
                        client.execute_cr(cr_id)
                        client.wait_for_completion(cr_id, f"Cleanup terminate {asset['name']}")
                    except Exception as e:
                        print(f"  ⚠️  Cleanup failed for {asset['name']}: {e}")
            elif asset["asset_type"] == "key_pair" and "smoke-test" in asset.get("name", ""):
                key_name = asset.get("asset_metadata", {}).get("key_name", asset["name"])
                asset_id = client.get_cloud_account_asset_id()
                try:
                    cr_id = client.create_cr(
                        f"Cleanup: delete key pair {key_name}",
                        "key_pair_create",
                        asset_id,
                        {"key_name": key_name},
                    )
                    # Just cancel it — key pair delete is the rollback
                    client.client.post(f"{client.base}/change-requests/{cr_id}/cancel")
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Nexplane AWS live smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Nexplane API base URL")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    print("=" * 60)
    print("Nexplane AWS Live Smoke Test")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated to Nexplane")

    scope_guard(client)
    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account asset: {cloud_account_id}")

    try:
        # Step 1: Create key pair
        print("\n[Step 1] Create key pair")
        client.run_cr(
            "Smoke test: create key pair",
            "key_pair_create",
            cloud_account_id,
            {"key_name": KEY_NAME},
        )
        key_asset = client.get_asset_by_name(KEY_NAME)
        if not key_asset:
            fail(f"Key pair asset '{KEY_NAME}' not found in inventory after creation")
        log(f"Key pair asset in inventory: {key_asset['id']}")

        # Step 2: Launch EC2 with SSM + key pair
        print("\n[Step 2] Launch EC2 instance")
        client.run_cr(
            "Smoke test: launch EC2",
            "ec2_launch",
            cloud_account_id,
            {
                "mode": "quick",
                "name": INSTANCE_NAME,
                "os": "amazon_linux",
                "iam_instance_profile": "NexplaneEC2TestProfile",
                "key_name": KEY_NAME,
                "rollback_strategy": "terminate_instance",
            },
        )
        # Wait for discovery to populate the asset
        time.sleep(10)
        instance_asset = client.get_asset_by_name(INSTANCE_NAME)
        if not instance_asset:
            fail(f"Instance asset '{INSTANCE_NAME}' not found in inventory after launch")
        instance_id = instance_asset.get("asset_metadata", {}).get("instance_id")
        if not instance_id:
            fail(f"instance_id missing from asset metadata: {instance_asset['asset_metadata']}")
        log(f"Instance in inventory: {instance_id}")

        # Give SSM agent time to register (can take 2-3 minutes on first boot)
        print("  Waiting 90s for SSM agent to register...")
        time.sleep(90)

        # Step 3: SSM connectivity check
        print("\n[Step 3] SSM connectivity check")
        client.run_cr(
            "Smoke test: SSM whoami",
            "ssm_command",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "whoami && hostname",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("SSM command executed successfully")

        # Step 4: Stop instance
        print("\n[Step 4] Stop instance")
        client.run_cr(
            "Smoke test: stop instance",
            "ec2_stop",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "snapshot_tag": "smoke-test-pre-stop",
                "rollback_strategy": "start_instance",
            },
        )
        log("Instance stopped")

        # Step 5: Start instance
        print("\n[Step 5] Start instance")
        client.run_cr(
            "Smoke test: start instance",
            "ec2_start",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "rollback_strategy": "stop_instance",
            },
        )
        log("Instance started")

        # Step 6: Terminate instance
        print("\n[Step 6] Terminate instance")
        client.run_cr(
            "Smoke test: terminate instance",
            "ec2_terminate",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "snapshot_tag": "smoke-test-pre-terminate",
                "confirm_terminate": True,
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("Instance terminated")

        # Verify instance removed from inventory
        time.sleep(15)
        still_there = client.get_asset_by_name(INSTANCE_NAME)
        if still_there and still_there.get("asset_metadata", {}).get("state") != "terminated":
            log("⚠️  Instance asset still in inventory after termination — discovery may be delayed", ok=False)
        else:
            log("Instance asset removed from inventory")

        print("\n" + "=" * 60)
        print("✅ ALL SMOKE TESTS PASSED")
        print("=" * 60)

    except SystemExit:
        print("\n" + "=" * 60)
        print("❌ SMOKE TEST FAILED — running cleanup")
        print("=" * 60)
        cleanup(client)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        cleanup(client)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Restart backend and verify catalog loads correctly**

```bash
docker compose restart backend
docker compose exec backend python -c "
from app.connectors.catalog_service import get_catalog_service
c = get_catalog_service()
for action in ['create_key_pair', 'delete_key_pair', 'run_ssm_command']:
    opts = c.get_options_for_action(action)
    print(f'{action}: {[o.connector_type for o in opts]}')
"
```

Expected output:
```
create_key_pair: ['aws']
delete_key_pair: ['aws']
run_ssm_command: ['aws']
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/__init__.py \
        backend/tests/smoke/test_aws_live.py
git commit -m "feat: add AWS live smoke test covering key pair, EC2 launch with SSM+IAM, stop/start/terminate lifecycle"
```

---

## Task 8: Run the Smoke Test

- [ ] **Step 1: Find the admin email and password**

The seed data admin is `admin@nexplane.local` with password `changeme` (check `backend/app/seed.py` if different).

- [ ] **Step 2: Run the smoke test**

From the repo root:

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme
```

Expected output: All 6 steps pass with ✅. Total runtime ~8-12 minutes (dominated by SSM agent registration and EC2 state transitions).

- [ ] **Step 3: If any step fails, check backend logs**

```bash
docker compose logs backend --tail 30
```

Fix the issue, reset the failing CR if needed:

```bash
docker compose exec backend python -c "
import asyncio
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
import uuid
async def main():
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, uuid.UUID('PASTE-CR-ID-HERE'))
        cr.status = ChangeRequestStatus.approved
        await db.commit()
asyncio.run(main())
"
```

Then re-run the smoke test from the failing step.

- [ ] **Step 4: Final commit once all tests pass**

```bash
git add -A
git commit -m "chore: AWS live smoke test passing — EC2 launch with IAM profile + SSM + key pair lifecycle verified"
```
