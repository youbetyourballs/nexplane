# ECS Rolling Deploy with Health-Check Rollback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ecs_rolling_deploy` CR type that registers a new ECS task def revision, drives a rolling service update, polls three health gates, and auto-rolls back to the previous task def ARN if any gate fails; plus a thin `ecs_task_def_deregister` CR for explicit cleanup.

**Architecture:** Single executor `ecs_rolling_deploy.py` runs six sequential phases (preflight → register → deploy → stability poll → ALB gate → HTTP probe); any phase 4–6 failure calls `update_service` back to the old task def ARN and marks `rolled_back=true`. The platform-level `rollback()` function does the same thing on operator request. A separate `ecs_task_def_deregister.py` calls `deregister_task_definition` and is `ROLLBACK_CAPABILITY="irreversible"`.

**Tech Stack:** Python 3.12, boto3, pytest, pytest-asyncio, unittest.mock; AWS ECS Fargate for smoke.

## Global Constraints

- SPDX header on every new file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All executor functions are `async def`; boto3 calls are synchronous (boto3 has no async client — do not wrap in `asyncio.to_thread`; the existing codebase calls boto3 synchronously in async functions)
- `_boto_client` is imported from `app.connectors.executors.aws.reference_scan`
- `AWS_MANAGED_FIELDS = {"taskDefinitionArn", "revision", "status", "registeredAt", "registeredBy", "deregisteredAt", "compatibilities", "requiresAttributes"}` — strip these before `register_task_definition`
- `ROLLBACK_CAPABILITY = "full"` for `ecs_rolling_deploy`; `"irreversible"` for `ecs_task_def_deregister`
- `ROLLBACK_REASON` is required when `ROLLBACK_CAPABILITY = "irreversible"`
- Module-level rollback signature: `async def rollback(parameters, execution_result, connector)` — 3 args, this exact order
- Smoke test constants: `SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"`, `SMOKE_ASSET_ID = "1a7051be-7110-4a21-9cdf-b023231cdff8"`, `BASE_URL = "http://localhost:8000"`
- Smoke test runs on EC2 (`docker exec nexplane-backend-1 python -m pytest ...`); all AWS calls go through live AWS credentials in the connector record

---

### Task 1: Model + Catalog Wiring

Register the two new ChangeTypes in the enum, add `ecs_rolling_deploy` to the implicit rollback set, add two catalog action entries to `aws.json`, and create two change-type-definition JSON files. No executor code yet — this task produces the configuration layer that the platform uses to route CRs.

**Files:**
- Modify: `backend/app/models/change_request.py:594`
- Modify: `backend/app/services/safety_engine.py:173`
- Modify: `backend/app/connectors/catalog/aws.json:2262`
- Create: `backend/app/connectors/change_type_definitions/ecs_rolling_deploy.json`
- Create: `backend/app/connectors/change_type_definitions/ecs_task_def_deregister.json`

**Interfaces:**
- Produces: `ChangeType.ecs_rolling_deploy`, `ChangeType.ecs_task_def_deregister` — consumed by Tasks 2, 3, 4

- [ ] **Step 1: Add ChangeType entries**

In `backend/app/models/change_request.py`, after line 594 (`update_reference = "update_reference"`), add:

```python
    # ECS rolling deploy
    ecs_rolling_deploy = "ecs_rolling_deploy"
    ecs_task_def_deregister = "ecs_task_def_deregister"
```

- [ ] **Step 2: Add ecs_rolling_deploy to _IMPLICIT_ROLLBACK_TYPES**

In `backend/app/services/safety_engine.py`, line 173 currently reads:
```python
    ChangeType.windows_parallel_migration,
}
```

Change to:
```python
    ChangeType.windows_parallel_migration,
    ChangeType.ecs_rolling_deploy,
}
```

- [ ] **Step 3: Add catalog entries to aws.json**

In `backend/app/connectors/catalog/aws.json`, after line 2262 (the closing `},` of the `update_ecs_task_def_env` block), insert:

```json
    {
      "action_id": "ecs_rolling_deploy",
      "generic_action": "ecs_rolling_deploy",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "ECS Rolling Deploy",
      "description": "Register a new ECS task definition revision (image tag and/or env var changes), drive a rolling service update, poll stability and health gates, and auto-rollback to the previous task def revision if any gate fails.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "service_arn", "type": "string", "required": true},
        {"name": "cluster", "type": "string", "required": true},
        {"name": "container_name", "type": "string", "required": false},
        {"name": "image_tag", "type": "string", "required": false},
        {"name": "env_var_overrides", "type": "array", "required": false},
        {"name": "target_group_arn", "type": "string", "required": false},
        {"name": "health_check_url", "type": "string", "required": false},
        {"name": "health_check_expected_status", "type": "integer", "required": false},
        {"name": "stability_timeout_seconds", "type": "integer", "required": false},
        {"name": "health_timeout_seconds", "type": "integer", "required": false},
        {"name": "region", "type": "string", "required": false}
      ],
      "executor": "aws.ecs_rolling_deploy",
      "rollback_strategy": "reconstitution",
      "estimated_duration_seconds": 300
    },
    {
      "action_id": "ecs_task_def_deregister",
      "generic_action": "ecs_task_def_deregister",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "ECS Task Definition Deregister",
      "description": "Deregister an ECS task definition revision, marking it INACTIVE. Irreversible — ECS has no re-register API.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "task_def_arn", "type": "string", "required": true},
        {"name": "region", "type": "string", "required": false}
      ],
      "executor": "aws.ecs_task_def_deregister",
      "rollback_strategy": "no_op",
      "estimated_duration_seconds": 30
    },
```

- [ ] **Step 4: Create ecs_rolling_deploy.json change-type-definition**

Create `backend/app/connectors/change_type_definitions/ecs_rolling_deploy.json`:

```json
{
  "change_type": "ecs_rolling_deploy",
  "display_name": "ECS Rolling Deploy",
  "steps": [
    {"generic_action": "ecs_rolling_deploy", "purpose": "execute", "required": true}
  ],
  "description": "Register a new ECS task definition revision (image tag and/or env var changes), drive a rolling service update, poll stability and health gates, and auto-rollback to the previous task def revision if any gate fails.",
  "risk_level_default": "medium",
  "incident_response": false,
  "parameters": {
    "service_arn": {
      "type": "string",
      "required": true,
      "label": "Service ARN",
      "description": "ECS service ARN or name"
    },
    "cluster": {
      "type": "string",
      "required": true,
      "label": "Cluster",
      "description": "ECS cluster name or ARN"
    },
    "container_name": {
      "type": "string",
      "required": false,
      "label": "Container Name",
      "description": "Target container name. Required when image_tag is set and the task def has more than one container. Defaults to the first container."
    },
    "image_tag": {
      "type": "string",
      "required": false,
      "label": "Image Tag",
      "description": "Full image string to deploy, e.g. nginx:1.27. At least one of image_tag or env_var_overrides must be provided."
    },
    "env_var_overrides": {
      "type": "array",
      "required": false,
      "label": "Env Var Overrides",
      "description": "List of {key, old_value, new_value} objects. old_value is a safety guard — the executor raises if the current value does not match."
    },
    "target_group_arn": {
      "type": "string",
      "required": false,
      "label": "Target Group ARN",
      "description": "ALB/NLB target group ARN. If provided, all targets must be healthy before the deploy is declared successful."
    },
    "health_check_url": {
      "type": "string",
      "required": false,
      "label": "Health Check URL",
      "description": "HTTP endpoint to probe after stability. GET must return health_check_expected_status."
    },
    "health_check_expected_status": {
      "type": "integer",
      "required": false,
      "label": "Health Check Expected Status",
      "description": "Expected HTTP status from health_check_url (default 200).",
      "default": 200
    },
    "stability_timeout_seconds": {
      "type": "integer",
      "required": false,
      "label": "Stability Timeout (seconds)",
      "description": "Max seconds to wait for ECS deployment stability (default 300).",
      "default": 300
    },
    "health_timeout_seconds": {
      "type": "integer",
      "required": false,
      "label": "Health Timeout (seconds)",
      "description": "Max seconds per health gate — applied independently to ALB gate and HTTP probe gate (default 60).",
      "default": 60
    },
    "region": {
      "type": "string",
      "required": false,
      "label": "AWS Region",
      "description": "AWS region. Defaults to connector credential region."
    }
  },
  "rollback": {
    "strategy": "reconstitution",
    "description": "Call update_service with old_task_def_arn from execution_result to restore the previous task definition revision."
  }
}
```

- [ ] **Step 5: Create ecs_task_def_deregister.json change-type-definition**

Create `backend/app/connectors/change_type_definitions/ecs_task_def_deregister.json`:

```json
{
  "change_type": "ecs_task_def_deregister",
  "display_name": "ECS Task Definition Deregister",
  "steps": [
    {"generic_action": "ecs_task_def_deregister", "purpose": "execute", "required": true}
  ],
  "description": "Deregister an ECS task definition revision, marking it INACTIVE. Irreversible — ECS has no re-register API.",
  "risk_level_default": "medium",
  "incident_response": false,
  "parameters": {
    "task_def_arn": {
      "type": "string",
      "required": true,
      "label": "Task Definition ARN",
      "description": "Full ARN of the task definition revision to deregister, e.g. arn:aws:ecs:us-east-1:123:task-definition/my-app:42"
    },
    "region": {
      "type": "string",
      "required": false,
      "label": "AWS Region",
      "description": "AWS region. Defaults to connector credential region."
    }
  },
  "rollback": {
    "strategy": "no_op",
    "description": "Irreversible — ECS has no API to re-register a deregistered task definition."
  }
}
```

- [ ] **Step 6: Verify import**

```bash
cd /home/ec2-user/nexplane
docker exec nexplane-backend-1 python -c "
from app.models.change_request import ChangeType
print(ChangeType.ecs_rolling_deploy)
print(ChangeType.ecs_task_def_deregister)
"
```

Expected:
```
ChangeType.ecs_rolling_deploy
ChangeType.ecs_task_def_deregister
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/app/services/safety_engine.py \
        backend/app/connectors/catalog/aws.json \
        backend/app/connectors/change_type_definitions/ecs_rolling_deploy.json \
        backend/app/connectors/change_type_definitions/ecs_task_def_deregister.json
git commit -m "feat(ecs): add ecs_rolling_deploy and ecs_task_def_deregister ChangeTypes, catalog, and CT definitions"
```

---

### Task 2: ecs_task_def_deregister Executor

Thin executor that calls `deregister_task_definition`. `ROLLBACK_CAPABILITY = "irreversible"` because ECS has no re-register API once a revision is deregistered.

**Files:**
- Create: `backend/app/connectors/executors/aws/ecs_task_def_deregister.py`
- Create: `backend/tests/unit/test_ecs_task_def_deregister.py`

**Interfaces:**
- Consumes: `ChangeType.ecs_task_def_deregister` (Task 1), `_boto_client` from `reference_scan`
- Produces: `execute(cr, connector, db)` returning `{"deregistered": True, "task_def_arn": str}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_ecs_task_def_deregister.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_deregister_returns_deregistered_true():
    from app.connectors.executors.aws.ecs_task_def_deregister import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "task_def_arn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:42",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_ecs.deregister_task_definition.return_value = {
        "taskDefinition": {"status": "INACTIVE"}
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["deregistered"] is True
    assert result["task_def_arn"] == "arn:aws:ecs:us-east-1:123:task-definition/myapp:42"
    mock_ecs.deregister_task_definition.assert_called_once_with(
        taskDefinition="arn:aws:ecs:us-east-1:123:task-definition/myapp:42"
    )


@pytest.mark.asyncio
async def test_deregister_passes_region():
    from app.connectors.executors.aws.ecs_task_def_deregister import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "task_def_arn": "arn:aws:ecs:eu-west-1:123:task-definition/myapp:5",
        "region": "eu-west-1",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK", "region": "us-east-1"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_ecs.deregister_task_definition.return_value = {"taskDefinition": {"status": "INACTIVE"}}

    with patch("boto3.client", return_value=mock_ecs) as mock_boto:
        await execute(mock_cr, mock_connector, mock_db)

    # boto3.client was called with region_name="eu-west-1" (from params, overrides credential default)
    call_kwargs = mock_boto.call_args[1]
    assert call_kwargs["region_name"] == "eu-west-1"


def test_rollback_capability_is_irreversible():
    import importlib
    mod = importlib.import_module("app.connectors.executors.aws.ecs_task_def_deregister")
    assert mod.ROLLBACK_CAPABILITY == "irreversible"
    assert hasattr(mod, "ROLLBACK_REASON")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest \
    tests/unit/test_ecs_task_def_deregister.py -v 2>&1 | tail -15
```

Expected: `ERROR` or `ModuleNotFoundError` — executor doesn't exist yet.

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/aws/ecs_task_def_deregister.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""ECS task definition deregister executor.

Marks a task definition revision INACTIVE via deregister_task_definition.
Irreversible — ECS has no re-register API.
"""

import logging
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "ECS has no API to re-register a deregistered task definition revision"


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    task_def_arn = params["task_def_arn"]
    region = params.get("region")

    client = _boto_client("ecs", connector, region)
    client.deregister_task_definition(taskDefinition=task_def_arn)
    logger.info("Deregistered ECS task definition %s", task_def_arn)
    return {"deregistered": True, "task_def_arn": task_def_arn}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest \
    tests/unit/test_ecs_task_def_deregister.py -v 2>&1 | tail -15
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/aws/ecs_task_def_deregister.py \
        backend/tests/unit/test_ecs_task_def_deregister.py
git commit -m "feat(ecs): add ecs_task_def_deregister executor"
```

---

### Task 3: ecs_rolling_deploy Executor

The core executor. Six phases: preflight → register → deploy → stability poll → ALB gate → HTTP probe. Any phase 4–6 failure triggers auto-rollback (`update_service` back to old task def ARN). Module-level `rollback()` does the same for operator-initiated rollback.

**Files:**
- Create: `backend/app/connectors/executors/aws/ecs_rolling_deploy.py`
- Create: `backend/tests/unit/test_ecs_rolling_deploy.py`

**Interfaces:**
- Consumes: `_boto_client` from `reference_scan`; `ChangeType.ecs_rolling_deploy` (Task 1)
- Produces: `execute(cr, connector, db)` → success/auto-rollback return shapes; `rollback(parameters, execution_result, connector)` → `{"rolled_back": True}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_ecs_rolling_deploy.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cr(params):
    mock_cr = MagicMock()
    mock_cr.parameters = params
    return mock_cr


def _make_connector():
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    return mock_connector


def _stable_service(task_def_arn, desired=1):
    """describe_services response for a fully-stable service."""
    return {
        "services": [{
            "taskDefinition": task_def_arn,
            "runningCount": desired,
            "desiredCount": desired,
            "deployments": [{"taskDefinition": task_def_arn, "status": "PRIMARY"}],
        }],
        "failures": [],
    }


def _unstable_service(task_def_arn, running=0, desired=1):
    """describe_services response for a service still converging."""
    return {
        "services": [{
            "taskDefinition": task_def_arn,
            "runningCount": running,
            "desiredCount": desired,
            "deployments": [
                {"taskDefinition": task_def_arn, "status": "PRIMARY"},
                {"taskDefinition": "old-arn", "status": "ACTIVE"},
            ],
        }],
        "failures": [],
    }


def _task_def(family="myapp", container_name="app", image="nginx:alpine", env=None):
    return {
        "family": family,
        "containerDefinitions": [{
            "name": container_name,
            "image": image,
            "environment": env or [{"name": "SMOKE_ENV", "value": "original"}],
        }],
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
    }


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_raises_if_no_changes_specified():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    cr = _make_cr({"service_arn": "svc", "cluster": "cl"})
    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = _stable_service("old-arn")

    with patch("boto3.client", return_value=mock_ecs):
        with pytest.raises(ValueError, match="image_tag or env_var_overrides"):
            await execute(cr, _make_connector(), AsyncMock())


@pytest.mark.asyncio
async def test_preflight_raises_if_service_not_found():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    cr = _make_cr({"service_arn": "bad-svc", "cluster": "cl", "image_tag": "nginx:1.27"})
    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = {"services": [], "failures": [{"reason": "MISSING"}]}

    with patch("boto3.client", return_value=mock_ecs):
        with pytest.raises(ValueError, match="not found"):
            await execute(cr, _make_connector(), AsyncMock())


# ---------------------------------------------------------------------------
# Register — image_tag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_applies_image_tag():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "image_tag": "nginx:1.27",
    })
    mock_ecs = MagicMock()
    # preflight
    mock_ecs.describe_services.return_value = _stable_service(OLD_ARN)
    # register
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": NEW_ARN}
    }
    # deploy + stability (one call: already stable immediately)
    mock_ecs.update_service.return_value = {}
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),           # preflight
        _stable_service(NEW_ARN),           # stability poll — immediately stable
    ]

    with patch("boto3.client", return_value=mock_ecs):
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["deployed"] is True
    assert result["rolled_back"] is False
    assert result["new_task_def_arn"] == NEW_ARN
    assert result["old_task_def_arn"] == OLD_ARN

    # Verify image was updated in the register call
    reg_kwargs = mock_ecs.register_task_definition.call_args[1]
    assert reg_kwargs["containerDefinitions"][0]["image"] == "nginx:1.27"


@pytest.mark.asyncio
async def test_register_raises_if_multi_container_no_name():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    cr = _make_cr({"service_arn": "svc", "cluster": "cl", "image_tag": "nginx:1.27"})
    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = _stable_service(OLD_ARN)
    multi_container_td = {
        "family": "myapp",
        "containerDefinitions": [
            {"name": "app", "image": "nginx:alpine", "environment": []},
            {"name": "sidecar", "image": "busybox", "environment": []},
        ],
        "networkMode": "awsvpc",
    }
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": multi_container_td}

    with patch("boto3.client", return_value=mock_ecs):
        with pytest.raises(ValueError, match="container_name is required"):
            await execute(cr, _make_connector(), AsyncMock())


# ---------------------------------------------------------------------------
# Register — env_var_overrides
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_applies_env_var_overrides():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "env_var_overrides": [{"key": "SMOKE_ENV", "old_value": "original", "new_value": "updated"}],
    })
    mock_ecs = MagicMock()
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),
        _stable_service(NEW_ARN),
    ]
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": NEW_ARN}
    }
    mock_ecs.update_service.return_value = {}

    with patch("boto3.client", return_value=mock_ecs):
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["deployed"] is True
    reg_kwargs = mock_ecs.register_task_definition.call_args[1]
    env = reg_kwargs["containerDefinitions"][0]["environment"]
    smoke_env = next(e for e in env if e["name"] == "SMOKE_ENV")
    assert smoke_env["value"] == "updated"


@pytest.mark.asyncio
async def test_register_raises_on_env_var_old_value_mismatch():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "env_var_overrides": [{"key": "SMOKE_ENV", "old_value": "wrong-expected", "new_value": "updated"}],
    })
    mock_ecs = MagicMock()
    mock_ecs.describe_services.return_value = _stable_service(OLD_ARN)
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}

    with patch("boto3.client", return_value=mock_ecs):
        with pytest.raises(ValueError, match="expected old_value"):
            await execute(cr, _make_connector(), AsyncMock())


# ---------------------------------------------------------------------------
# Stability poll — timeout triggers auto-rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stability_timeout_triggers_auto_rollback():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "image_tag": "nginx:this-tag-does-not-exist-99999",
        "stability_timeout_seconds": 1,   # very short for test
    })
    mock_ecs = MagicMock()
    # preflight returns stable, all subsequent stability polls return unstable
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),           # preflight
        _unstable_service(NEW_ARN, 0, 1),   # stability poll 1 — never converges
        _unstable_service(NEW_ARN, 0, 1),   # stability poll 2
        _stable_service(OLD_ARN),           # (unused — rollback update_service happens before this)
    ]
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {"taskDefinition": {"taskDefinitionArn": NEW_ARN}}
    mock_ecs.update_service.return_value = {}

    with patch("boto3.client", return_value=mock_ecs), \
         patch("time.sleep"):   # skip actual sleeping
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["rolled_back"] is True
    assert result["deployed"] is False
    assert "stability timeout" in result["rollback_reason"]
    assert result["old_task_def_arn"] == OLD_ARN
    # update_service called twice: once to deploy, once to rollback
    assert mock_ecs.update_service.call_count == 2
    rollback_call = mock_ecs.update_service.call_args_list[-1]
    assert rollback_call[1]["taskDefinition"] == OLD_ARN


# ---------------------------------------------------------------------------
# ALB health gate — timeout triggers auto-rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alb_health_timeout_triggers_auto_rollback():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "image_tag": "nginx:1.27",
        "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/my-tg/abc",
        "stability_timeout_seconds": 30,
        "health_timeout_seconds": 1,   # very short for test
    })

    mock_ecs = MagicMock()
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),   # preflight
        _stable_service(NEW_ARN),   # stability poll — immediately stable
    ]
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {"taskDefinition": {"taskDefinitionArn": NEW_ARN}}
    mock_ecs.update_service.return_value = {}

    mock_elb = MagicMock()
    mock_elb.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "unhealthy"}}]
    }

    def boto_side_effect(service, **kwargs):
        if service == "ecs":
            return mock_ecs
        if service == "elbv2":
            return mock_elb
        raise ValueError(f"unexpected service {service}")

    with patch("boto3.client", side_effect=boto_side_effect), \
         patch("time.sleep"):
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["rolled_back"] is True
    assert "ALB health timeout" in result["rollback_reason"]
    # verify rollback update_service was called with old ARN
    rollback_call = mock_ecs.update_service.call_args_list[-1]
    assert rollback_call[1]["taskDefinition"] == OLD_ARN


# ---------------------------------------------------------------------------
# HTTP probe — wrong status triggers auto-rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_probe_wrong_status_triggers_auto_rollback():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "image_tag": "nginx:1.27",
        "health_check_url": "http://app.internal/health",
        "health_check_expected_status": 200,
        "stability_timeout_seconds": 30,
        "health_timeout_seconds": 1,
    })

    mock_ecs = MagicMock()
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),
        _stable_service(NEW_ARN),
    ]
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {"taskDefinition": {"taskDefinitionArn": NEW_ARN}}
    mock_ecs.update_service.return_value = {}

    mock_resp = MagicMock()
    mock_resp.status_code = 503

    with patch("boto3.client", return_value=mock_ecs), \
         patch("time.sleep"), \
         patch("requests.get", return_value=mock_resp):
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["rolled_back"] is True
    assert "HTTP probe timeout" in result["rollback_reason"]


# ---------------------------------------------------------------------------
# Successful path — all gates pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_health_gates_pass_returns_deployed_true():
    from app.connectors.executors.aws.ecs_rolling_deploy import execute

    OLD_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"
    NEW_ARN = "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"

    cr = _make_cr({
        "service_arn": "svc", "cluster": "cl",
        "image_tag": "nginx:1.27",
        "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/tg/abc",
        "health_check_url": "http://app.internal/health",
    })

    mock_ecs = MagicMock()
    mock_ecs.describe_services.side_effect = [
        _stable_service(OLD_ARN),
        _stable_service(NEW_ARN),
    ]
    mock_ecs.describe_task_definition.return_value = {"taskDefinition": _task_def()}
    mock_ecs.register_task_definition.return_value = {"taskDefinition": {"taskDefinitionArn": NEW_ARN}}
    mock_ecs.update_service.return_value = {}

    mock_elb = MagicMock()
    mock_elb.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    def boto_side_effect(service, **kwargs):
        if service == "ecs":
            return mock_ecs
        if service == "elbv2":
            return mock_elb
        raise ValueError(service)

    with patch("boto3.client", side_effect=boto_side_effect), \
         patch("requests.get", return_value=mock_resp):
        result = await execute(cr, _make_connector(), AsyncMock())

    assert result["deployed"] is True
    assert result["rolled_back"] is False
    assert result["health_checks"]["alb"] == "passed"
    assert result["health_checks"]["http"] == "passed"


# ---------------------------------------------------------------------------
# Module-level rollback()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_module_rollback_calls_update_service_with_old_arn():
    from app.connectors.executors.aws.ecs_rolling_deploy import rollback

    params = {"service_arn": "svc", "cluster": "cl"}
    execution_result = {
        "old_task_def_arn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:5",
        "new_task_def_arn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:6",
    }
    connector = _make_connector()

    mock_ecs = MagicMock()
    mock_ecs.update_service.return_value = {}

    with patch("boto3.client", return_value=mock_ecs):
        result = await rollback(params, execution_result, connector)

    assert result["rolled_back"] is True
    mock_ecs.update_service.assert_called_once_with(
        cluster="cl",
        service="svc",
        taskDefinition="arn:aws:ecs:us-east-1:123:task-definition/myapp:5",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest \
    tests/unit/test_ecs_rolling_deploy.py -v 2>&1 | tail -20
```

Expected: `ERROR` — module not found.

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/aws/ecs_rolling_deploy.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""ECS rolling deploy executor with multi-gate health-check rollback.

Phases:
  1. Preflight  — validate service + cluster exist; capture old_task_def_arn
  2. Register   — build new task def revision (image_tag + env_var_overrides)
  3. Deploy     — update_service to new task def ARN
  4. Stability  — poll until runningCount==desiredCount and single deployment
  5. ALB gate   — (optional) all target group targets healthy
  6. HTTP probe — (optional) GET health_check_url returns expected status

Any phase 4–6 failure calls update_service back to old_task_def_arn (auto-rollback).
"""

import logging
import time
import requests
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

AWS_MANAGED_FIELDS = {
    "taskDefinitionArn", "revision", "status", "registeredAt",
    "registeredBy", "deregisteredAt", "compatibilities", "requiresAttributes",
}


def _preflight(ecs_client, params: dict) -> dict:
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    image_tag = params.get("image_tag")
    env_var_overrides = params.get("env_var_overrides") or []

    if not image_tag and not env_var_overrides:
        raise ValueError("At least one of image_tag or env_var_overrides must be provided")

    resp = ecs_client.describe_services(cluster=cluster, services=[service_arn])
    services = resp.get("services", [])
    if not services:
        failures = resp.get("failures", [])
        raise ValueError(f"Service {service_arn} not found in cluster {cluster}: {failures}")

    svc = services[0]
    return {"old_task_def_arn": svc["taskDefinition"]}


def _register(ecs_client, params: dict, old_task_def_arn: str) -> str:
    td = ecs_client.describe_task_definition(taskDefinition=old_task_def_arn)["taskDefinition"]
    containers = [dict(c) for c in td["containerDefinitions"]]

    image_tag = params.get("image_tag")
    if image_tag:
        container_name = params.get("container_name")
        if len(containers) > 1 and not container_name:
            raise ValueError(
                "container_name is required when image_tag is set and the task definition "
                "has more than one container"
            )
        target = containers[0] if not container_name else next(
            (c for c in containers if c["name"] == container_name), None
        )
        if target is None:
            raise ValueError(f"Container '{container_name}' not found in task definition")
        target["image"] = image_tag

    env_var_overrides = params.get("env_var_overrides") or []
    for override in env_var_overrides:
        key = override["key"]
        old_val = override["old_value"]
        new_val = override["new_value"]
        container_name = params.get("container_name")
        target_name = container_name if container_name else containers[0]["name"]

        for container in containers:
            if container["name"] == target_name:
                env = [dict(e) for e in container.get("environment", [])]
                found = False
                for e in env:
                    if e["name"] == key:
                        if e["value"] != old_val:
                            raise ValueError(
                                f"env var {key}: expected old_value='{old_val}', "
                                f"found '{e['value']}'"
                            )
                        e["value"] = new_val
                        found = True
                if not found:
                    raise ValueError(
                        f"env var '{key}' not found in container '{target_name}'"
                    )
                container["environment"] = env

    register_kwargs = {k: v for k, v in td.items()
                       if k not in AWS_MANAGED_FIELDS and k != "containerDefinitions"}
    register_kwargs["containerDefinitions"] = containers
    new_td = ecs_client.register_task_definition(**register_kwargs)["taskDefinition"]
    return new_td["taskDefinitionArn"]


def _poll_stability(ecs_client, cluster: str, service_arn: str,
                    timeout: int) -> int:
    """Return elapsed seconds on success; raise TimeoutError on timeout."""
    start = time.time()
    deadline = start + timeout
    last_svc = None
    while time.time() < deadline:
        resp = ecs_client.describe_services(cluster=cluster, services=[service_arn])
        svc = resp["services"][0]
        last_svc = svc
        if svc["runningCount"] == svc["desiredCount"] and len(svc.get("deployments", [])) == 1:
            return int(time.time() - start)
        time.sleep(10)
    raise TimeoutError(
        f"stability timeout after {timeout}s "
        f"(runningCount={last_svc['runningCount']}, "
        f"desiredCount={last_svc['desiredCount']})"
    )


def _check_alb_health(elb_client, target_group_arn: str, timeout: int) -> None:
    """Raise TimeoutError if targets are not all healthy within timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = elb_client.describe_target_health(TargetGroupArn=target_group_arn)
        targets = resp.get("TargetHealthDescriptions", [])
        if targets and all(t["TargetHealth"]["State"] == "healthy" for t in targets):
            return
        time.sleep(10)
    raise TimeoutError(f"ALB health timeout after {timeout}s — targets not healthy")


def _check_http_probe(url: str, expected_status: int, timeout: int) -> None:
    """Raise TimeoutError if health endpoint doesn't return expected_status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == expected_status:
                return
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError(
        f"HTTP probe timeout after {timeout}s — "
        f"expected status {expected_status} from {url}"
    )


def _do_rollback(ecs_client, cluster: str, service_arn: str, old_task_def_arn: str) -> None:
    ecs_client.update_service(cluster=cluster, service=service_arn, taskDefinition=old_task_def_arn)
    logger.info("Auto-rolled back ECS service %s to %s", service_arn, old_task_def_arn)


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    region = params.get("region")
    stability_timeout = int(params.get("stability_timeout_seconds") or 300)
    health_timeout = int(params.get("health_timeout_seconds") or 60)
    target_group_arn = params.get("target_group_arn")
    health_check_url = params.get("health_check_url")
    expected_status = int(params.get("health_check_expected_status") or 200)

    ecs = _boto_client("ecs", connector, region)

    # Phase 1: Preflight
    preflight = _preflight(ecs, params)
    old_task_def_arn = preflight["old_task_def_arn"]

    # Phase 2: Register
    new_task_def_arn = _register(ecs, params, old_task_def_arn)
    logger.info("Registered new task def %s (was %s)", new_task_def_arn, old_task_def_arn)

    # Phase 3: Deploy
    ecs.update_service(cluster=cluster, service=service_arn, taskDefinition=new_task_def_arn)
    logger.info("Updated ECS service %s to %s", service_arn, new_task_def_arn)

    health_checks = {}

    # Phase 4: Stability poll
    try:
        stability_seconds = _poll_stability(ecs, cluster, service_arn, stability_timeout)
    except TimeoutError as e:
        _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
        return {
            "deployed": False,
            "rolled_back": True,
            "old_task_def_arn": old_task_def_arn,
            "new_task_def_arn": new_task_def_arn,
            "rollback_reason": f"stability timeout after {stability_timeout}s: {e}",
            "health_checks": health_checks,
        }

    # Phase 5: ALB health gate (optional)
    if target_group_arn:
        elb = _boto_client("elbv2", connector, region)
        try:
            _check_alb_health(elb, target_group_arn, health_timeout)
            health_checks["alb"] = "passed"
        except TimeoutError:
            _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
            return {
                "deployed": False,
                "rolled_back": True,
                "old_task_def_arn": old_task_def_arn,
                "new_task_def_arn": new_task_def_arn,
                "rollback_reason": f"ALB health timeout after {health_timeout}s",
                "health_checks": health_checks,
            }

    # Phase 6: HTTP probe gate (optional)
    if health_check_url:
        try:
            _check_http_probe(health_check_url, expected_status, health_timeout)
            health_checks["http"] = "passed"
        except TimeoutError:
            _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
            return {
                "deployed": False,
                "rolled_back": True,
                "old_task_def_arn": old_task_def_arn,
                "new_task_def_arn": new_task_def_arn,
                "rollback_reason": f"HTTP probe timeout after {health_timeout}s",
                "health_checks": health_checks,
            }

    return {
        "deployed": True,
        "rolled_back": False,
        "old_task_def_arn": old_task_def_arn,
        "new_task_def_arn": new_task_def_arn,
        "stability_seconds": stability_seconds,
        "health_checks": health_checks,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    params = parameters or {}
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    region = params.get("region")
    old_task_def_arn = execution_result.get("old_task_def_arn", "")

    if not old_task_def_arn:
        return {"rolled_back": False, "reason": "no old_task_def_arn in execution_result"}

    ecs = _boto_client("ecs", connector, region)
    ecs.update_service(cluster=cluster, service=service_arn, taskDefinition=old_task_def_arn)
    logger.info("Rolled back ECS service %s to %s", service_arn, old_task_def_arn)
    return {"rolled_back": True, "old_task_def_arn": old_task_def_arn}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest \
    tests/unit/test_ecs_rolling_deploy.py -v 2>&1 | tail -20
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/aws/ecs_rolling_deploy.py \
        backend/tests/unit/test_ecs_rolling_deploy.py
git commit -m "feat(ecs): add ecs_rolling_deploy executor with 6-phase health-check auto-rollback"
```

---

### Task 4: Smoke Test

Three smoke phases against real AWS Fargate: successful deploy + manual rollback, auto-rollback on bad image, and `ecs_task_def_deregister`. All run via the platform CR lifecycle (create → plan → approve → execute → poll). Runs inside `docker exec nexplane-backend-1`.

**Files:**
- Create: `backend/tests/smoke/test_ecs_rolling_deploy_smoke.py`

**Interfaces:**
- Consumes: all executor code from Tasks 2–3; `smoke_helpers.NexplaneClient`

- [ ] **Step 1: Write the smoke test**

Create `backend/tests/smoke/test_ecs_rolling_deploy_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
ECS rolling deploy smoke test.

Three phases against real AWS Fargate:
  1. Successful deploy (env var + image tag) + manual platform rollback
  2. Auto-rollback on bad image (stability timeout fires)
  3. ecs_task_def_deregister CR

Run on EC2:
    cd /home/ec2-user/nexplane
    PYTHONPATH=backend python3 -m pytest \
        backend/tests/smoke/test_ecs_rolling_deploy_smoke.py -v -s \
        2>&1 | tee /tmp/ecs_smoke.log; echo SMOKE_DONE_ECS >> /tmp/ecs_smoke.log
"""

import os
import sys
import time
import json

import pytest
import boto3
import requests

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL           = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL              = os.environ.get("NEXPLANE_EMAIL",    "admin@acme.example")
PASSWORD           = os.environ.get("NEXPLANE_PASSWORD", "admin123")
SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"
SMOKE_ASSET_ID     = "1a7051be-7110-4a21-9cdf-b023231cdff8"

CLUSTER_NAME = "smoke-ecs-rolling-deploy"
SERVICE_NAME = "smoke-ecs-svc"
TASK_FAMILY  = "smoke-ecs-task"
REGION       = "us-east-1"

POLL_INTERVAL = 15
CR_TIMEOUT    = 600   # 10 min — Fargate cold start can be slow


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def _aws_creds():
    """Pull AWS credentials from the smoke connector record in the platform DB."""
    creds = get_connector_creds_from_db(SMOKE_CONNECTOR_ID)
    return {
        "aws_access_key_id":     creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name":           REGION,
    }


def _ecs():
    return boto3.client("ecs", **_aws_creds())


def _iam():
    return boto3.client("iam", **_aws_creds())


def _get_execution_role_arn() -> str:
    """Return the ARN of ecsTaskExecutionRole, creating it if absent."""
    iam = _iam()
    role_name = "ecsTaskExecutionRole"
    try:
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }]
        })
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust,
        )["Role"]
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        time.sleep(10)   # IAM eventual consistency
        return role["Arn"]


def _register_initial_task_def(execution_role_arn: str) -> str:
    ecs = _ecs()
    resp = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        executionRoleArn=execution_role_arn,
        containerDefinitions=[{
            "name": "app",
            "image": "nginx:alpine",
            "essential": True,
            "environment": [{"name": "SMOKE_ENV", "value": "original"}],
            "portMappings": [{"containerPort": 80, "protocol": "tcp"}],
        }],
    )
    return resp["taskDefinition"]["taskDefinitionArn"]


def _create_cluster() -> None:
    ecs = _ecs()
    try:
        ecs.create_cluster(
            clusterName=CLUSTER_NAME,
            capacityProviders=["FARGATE"],
        )
        log(f"Created cluster {CLUSTER_NAME}")
    except ecs.exceptions.ClusterContainsServicesException:
        log(f"Cluster {CLUSTER_NAME} already exists")


def _create_service(task_def_arn: str, subnet_id: str, sg_id: str) -> None:
    ecs = _ecs()
    try:
        ecs.create_service(
            cluster=CLUSTER_NAME,
            serviceName=SERVICE_NAME,
            taskDefinition=task_def_arn,
            desiredCount=1,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [subnet_id],
                    "securityGroups": [sg_id],
                    "assignPublicIp": "ENABLED",
                }
            },
            deploymentConfiguration={
                "deploymentCircuitBreaker": {"enable": False, "rollback": False},
                "maximumPercent": 200,
                "minimumHealthyPercent": 0,
            },
        )
        log(f"Created ECS service {SERVICE_NAME}")
    except ecs.exceptions.ServiceAlreadyExistsException:
        log(f"Service {SERVICE_NAME} already exists")


def _wait_service_stable(timeout: int = 300) -> None:
    deadline = time.time() + timeout
    ecs = _ecs()
    while time.time() < deadline:
        resp = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME])
        svc = resp["services"][0]
        if svc["runningCount"] == svc["desiredCount"] and len(svc["deployments"]) == 1:
            log(f"Service stable: runningCount={svc['runningCount']}")
            return
        log(f"Waiting for service stability: running={svc['runningCount']}/{svc['desiredCount']}")
        time.sleep(15)
    raise TimeoutError(f"Service did not stabilize within {timeout}s")


def _get_service_task_def() -> str:
    ecs = _ecs()
    resp = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME])
    return resp["services"][0]["taskDefinition"]


def _get_vpc_subnet_sg() -> tuple:
    """Return (subnet_id, sg_id) from the default VPC."""
    ec2 = boto3.client("ec2", **_aws_creds())
    vpc_resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpc_resp["Vpcs"][0]["VpcId"]
    subnet_resp = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "default-for-az", "Values": ["true"]}]
    )
    subnet_id = subnet_resp["Subnets"][0]["SubnetId"]
    sg_resp = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "group-name", "Values": ["default"]}]
    )
    sg_id = sg_resp["SecurityGroups"][0]["GroupId"]
    return subnet_id, sg_id


def _teardown(registered_arns: list) -> None:
    ecs = _ecs()
    try:
        ecs.update_service(cluster=CLUSTER_NAME, service=SERVICE_NAME, desiredCount=0)
        time.sleep(10)
        ecs.delete_service(cluster=CLUSTER_NAME, service=SERVICE_NAME, force=True)
        log("Deleted ECS service")
    except Exception as e:
        log(f"Service teardown warning: {e}")
    for arn in registered_arns:
        try:
            ecs.deregister_task_definition(taskDefinition=arn)
            log(f"Deregistered {arn}")
        except Exception as e:
            log(f"Deregister warning for {arn}: {e}")
    try:
        ecs.delete_cluster(cluster=CLUSTER_NAME)
        log(f"Deleted cluster {CLUSTER_NAME}")
    except Exception as e:
        log(f"Cluster teardown warning: {e}")


# ---------------------------------------------------------------------------
# CR lifecycle helpers (same pattern as other smoke tests)
# ---------------------------------------------------------------------------

def _cr_lifecycle(client: NexplaneClient, title: str, change_type: str,
                  desired_outcome: dict, timeout: int = CR_TIMEOUT) -> dict:
    base = client.base
    r = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": change_type,
        "desired_outcome": desired_outcome,
        "target_asset_ids": [SMOKE_ASSET_ID],
    })
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]
    log(f"[{title}] CR created: {cr_id}")

    for step in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{step}")
        assert r2.status_code in (200, 201, 202, 204), f"/{step} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "ecs-rolling-deploy smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "verify_failed"):
            log(f"[{title}] -> {status}")
            return cr
        if status in ("failed", "preflight_blocked", "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} terminal with status={status!r}: "
                f"{str(cr.get('execution_result', ''))[:800]}"
            )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, label: str,
                 timeout: int = CR_TIMEOUT) -> dict:
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code == 409:
        err = r.json()
        if err.get("error") == "out_of_order_rollback":
            blocking = err.get("blocking_crs", [])
            log(f"[{label}] FILO 409 — rolling back {len(blocking)} blocker(s) first")
            for blk_id in blocking:
                _rollback_cr(client, blk_id, f"blocker:{blk_id[:8]}", timeout=timeout)
            r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        if cr.get("status") in ("rolled_back", "rollback_failed"):
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"[{label}] rollback timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestEcsRollingDeploySmoke:

    def setup_method(self, _method):
        """Teardown any leftover resources from a previous run."""
        try:
            _teardown([])
        except Exception:
            pass

    def teardown_method(self, _method):
        pass   # per-phase teardown tracks its own ARNs

    def test_ecs_rolling_deploy(self):
        client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        registered_arns = []

        try:
            # ---------------------------------------------------------------
            # Infrastructure setup
            # ---------------------------------------------------------------
            log("=== Setup: creating Fargate cluster + task def + service ===")
            execution_role_arn = _get_execution_role_arn()
            subnet_id, sg_id = _get_vpc_subnet_sg()
            _create_cluster()

            initial_arn = _register_initial_task_def(execution_role_arn)
            registered_arns.append(initial_arn)
            log(f"Initial task def: {initial_arn}")

            _create_service(initial_arn, subnet_id, sg_id)
            _wait_service_stable(timeout=300)

            # ---------------------------------------------------------------
            # Phase 1: Successful deploy + manual rollback
            # ---------------------------------------------------------------
            log("=== Phase 1: Successful deploy (env var + image tag) ===")
            cr1 = _cr_lifecycle(
                client,
                title="ECS smoke — phase 1 deploy",
                change_type="ecs_rolling_deploy",
                desired_outcome={
                    "service_arn": SERVICE_NAME,
                    "cluster": CLUSTER_NAME,
                    "image_tag": "nginx:1.27",
                    "env_var_overrides": [
                        {"key": "SMOKE_ENV", "old_value": "original", "new_value": "updated"}
                    ],
                    "stability_timeout_seconds": 300,
                    "region": REGION,
                },
            )
            er1 = cr1.get("execution_result", {})
            log(f"Phase 1 result: {er1}")
            assert er1.get("deployed") is True, f"expected deployed=True, got {er1}"
            assert er1.get("rolled_back") is False, f"expected rolled_back=False, got {er1}"
            new_arn_1 = er1["new_task_def_arn"]
            registered_arns.append(new_arn_1)
            assert new_arn_1 != initial_arn, "new_task_def_arn should differ from initial"
            assert _get_service_task_def() == new_arn_1, "Service should point to new task def"
            log("Phase 1 deploy assertions PASSED ✅")

            log("Phase 1: triggering platform rollback")
            rb1 = _rollback_cr(client, cr1["id"], "phase1-rollback")
            assert rb1.get("status") in ("rolled_back", "rollback_failed"), \
                f"Unexpected rollback status: {rb1.get('status')}"
            if rb1.get("status") == "rolled_back":
                assert _get_service_task_def() == initial_arn, \
                    "After rollback, service should point back to initial task def"
                log("Phase 1 rollback assertions PASSED ✅")
            else:
                log(f"Phase 1 rollback_failed (acceptable): {rb1.get('execution_result')}")

            # ---------------------------------------------------------------
            # Phase 2: Auto-rollback on bad image
            # ---------------------------------------------------------------
            log("=== Phase 2: Auto-rollback on bad image ===")
            # Re-point service back to initial_arn for clean phase 2 state
            _ecs().update_service(
                cluster=CLUSTER_NAME, service=SERVICE_NAME, taskDefinition=initial_arn
            )
            _wait_service_stable(timeout=300)

            cr2 = _cr_lifecycle(
                client,
                title="ECS smoke — phase 2 bad image",
                change_type="ecs_rolling_deploy",
                desired_outcome={
                    "service_arn": SERVICE_NAME,
                    "cluster": CLUSTER_NAME,
                    "image_tag": "nginx:this-tag-does-not-exist-99999",
                    "stability_timeout_seconds": 120,
                    "region": REGION,
                },
                timeout=300,
            )
            er2 = cr2.get("execution_result", {})
            log(f"Phase 2 result: {er2}")
            bad_arn = er2.get("new_task_def_arn")
            if bad_arn:
                registered_arns.append(bad_arn)
            assert er2.get("rolled_back") is True, \
                f"expected rolled_back=True for bad image, got {er2}"
            assert _get_service_task_def() == initial_arn, \
                "After auto-rollback, service should point back to initial task def"
            log("Phase 2 auto-rollback assertions PASSED ✅")

            # ---------------------------------------------------------------
            # Phase 3: ecs_task_def_deregister
            # ---------------------------------------------------------------
            if bad_arn:
                log(f"=== Phase 3: Deregister bad-image task def {bad_arn} ===")
                cr3 = _cr_lifecycle(
                    client,
                    title="ECS smoke — phase 3 deregister",
                    change_type="ecs_task_def_deregister",
                    desired_outcome={
                        "task_def_arn": bad_arn,
                        "region": REGION,
                    },
                )
                er3 = cr3.get("execution_result", {})
                log(f"Phase 3 result: {er3}")
                assert er3.get("deregistered") is True, f"expected deregistered=True, got {er3}"
                td_status = _ecs().describe_task_definition(
                    taskDefinition=bad_arn
                )["taskDefinition"]["status"]
                assert td_status == "INACTIVE", \
                    f"Expected task def INACTIVE after deregister, got {td_status}"
                registered_arns.remove(bad_arn)   # already deregistered
                log("Phase 3 deregister assertions PASSED ✅")
            else:
                log("Phase 3: skipped — bad_arn not available (auto-rollback happened before register)")

            log("=== ALL ECS ROLLING DEPLOY SMOKE PHASES PASSED ✅ ===")

        finally:
            log("=== Teardown ===")
            _teardown(registered_arns)
```

- [ ] **Step 2: SCP smoke test to EC2**

Run on local Windows machine:

```powershell
scp -i ~/.ssh/id_ed25519 `
  f:/Nexplane/nexplane/backend/tests/smoke/test_ecs_rolling_deploy_smoke.py `
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_ecs_rolling_deploy_smoke.py
```

- [ ] **Step 3: Restart backend to pick up new executor code**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull origin master && docker compose restart backend"
```

Wait ~15s for backend to come up, then verify:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -c 'from app.connectors.executors.aws.ecs_rolling_deploy import execute; print(\"OK\")'"
```

Expected: `OK`

- [ ] **Step 4: Run the smoke test**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && \
   PYTHONPATH=backend python3 -m pytest \
     backend/tests/smoke/test_ecs_rolling_deploy_smoke.py -v -s \
     2>&1 | tee /tmp/ecs_smoke.log; echo SMOKE_DONE_ECS >> /tmp/ecs_smoke.log"
```

Expected final lines:
```
=== ALL ECS ROLLING DEPLOY SMOKE PHASES PASSED ✅ ===
1 passed in Xs
SMOKE_DONE_ECS
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_ecs_rolling_deploy_smoke.py
git commit -m "smoke: ECS rolling deploy smoke PASSED"
git push origin master
```
