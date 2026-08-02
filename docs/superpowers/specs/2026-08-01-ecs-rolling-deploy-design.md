# ECS Rolling Deploy with Health-Check Rollback — Design Spec

## Goal

Add an `ecs_rolling_deploy` CR type that registers a new ECS task definition revision (image tag change, env var overrides, or both), drives a rolling service update, polls multiple health gates, and automatically rolls back to the previous task definition revision if any gate fails. Also adds a thin `ecs_task_def_deregister` CR for explicit cleanup.

This unblocks the "rolling deploy with auto-rollback" story for ECS containers and replaces the current `manual_action_required` rollback status on `update_ecs_task_def_env`.

## Background

`update_ecs_task_def_env` (in `reference_update.py`) registers a new task def revision with updated env vars but never calls `update_service`. ECS services continue running old tasks until explicitly updated. Rollback is therefore `manual_action_required`. The new CR closes this gap.

The existing executor is left untouched — it serves the `credential_rotation_fanout` use case where only the new task def ARN is needed, not a live deploy.

## Architecture

Single executor: `backend/app/connectors/executors/aws/ecs_rolling_deploy.py`

Six sequential phases:

1. **Preflight** — validate `service_arn` + `cluster` exist via `describe_services`; confirm at least one of `image_tag` or `env_var_overrides` is provided; fetch current service's active task def ARN → saved as `old_task_def_arn` for rollback.

2. **Register** — fetch current task def via `describe_task_definition`; apply `image_tag` to the target container (identified by `container_name`, defaulting to first container if only one exists); apply `env_var_overrides` with the same `old_value` safety guard as the existing executor (raises if current value doesn't match); strip AWS-managed fields; call `register_task_definition` → `new_task_def_arn`.

3. **Deploy** — call `update_service(cluster=..., service=..., taskDefinition=new_task_def_arn)`.

4. **Stability poll** — every 10s call `describe_services`; pass when `runningCount == desiredCount` and `len(deployments) == 1`; auto-rollback if `stability_timeout_seconds` exceeded.

5. **ALB/NLB health gate** *(skipped if `target_group_arn` not provided)* — every 10s call `describe_target_health`; pass when all targets are `healthy`; auto-rollback if `health_timeout_seconds` exceeded.

6. **HTTP probe gate** *(skipped if `health_check_url` not provided)* — GET `health_check_url`; pass on `health_check_expected_status` (default 200); retry every 5s; auto-rollback on timeout or wrong status.

**Auto-rollback** (triggered by any phase 4–6 failure): call `update_service` back to `old_task_def_arn`; set `rolled_back: true` in result. Does not deregister `new_task_def_arn` — that is the operator's responsibility via `ecs_task_def_deregister`.

**Platform rollback** (`rollback()` module function): reads `old_task_def_arn` from `execution_result` and calls `update_service` back to it. Works for both successful deploys (operator-initiated rollback) and failed deploys where auto-rollback itself failed.

## Parameters

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `service_arn` | string | yes | — | ECS service ARN or name |
| `cluster` | string | yes | — | Cluster name or ARN |
| `container_name` | string | no | first container | Required when `image_tag` is set and task def has >1 container |
| `image_tag` | string | no | — | Full image string e.g. `nginx:1.27`; at least one of this or `env_var_overrides` required |
| `env_var_overrides` | array of `{key, old_value, new_value}` | no | — | Same safety-guard pattern as existing executor |
| `target_group_arn` | string | no | — | Enables ALB/NLB health check gate |
| `health_check_url` | string | no | — | Enables HTTP probe gate |
| `health_check_expected_status` | int | no | `200` | |
| `stability_timeout_seconds` | int | no | `300` | |
| `health_timeout_seconds` | int | no | `60` | Budget applied independently to ALB gate and HTTP probe gate |
| `region` | string | no | — | |

## Return Shape

**Success:**
```json
{
  "deployed": true,
  "rolled_back": false,
  "old_task_def_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app:41",
  "new_task_def_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app:42",
  "stability_seconds": 87,
  "health_checks": {
    "alb": "passed",
    "http": "passed"
  }
}
```

**Auto-rollback fired:**
```json
{
  "deployed": false,
  "rolled_back": true,
  "old_task_def_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app:41",
  "new_task_def_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/my-app:42",
  "rollback_reason": "stability timeout after 300s (runningCount=0, desiredCount=1)",
  "health_checks": {}
}
```

## `ecs_task_def_deregister` CR

Thin executor: `backend/app/connectors/executors/aws/ecs_task_def_deregister.py`

Parameters: `task_def_arn` (string, required), `region` (string, optional).

Calls `deregister_task_definition(taskDefinition=task_def_arn)`. Returns `{"deregistered": true, "task_def_arn": "..."}`.

`ROLLBACK_CAPABILITY = "irreversible"` — ECS has no re-register API.

## Catalog & ChangeType

**`backend/app/models/change_request.py`** — two new `ChangeType` enum entries:
- `ecs_rolling_deploy`
- `ecs_task_def_deregister`

**`backend/app/services/safety_engine.py`** — `ecs_rolling_deploy` added to `_IMPLICIT_ROLLBACK_TYPES`; `ecs_task_def_deregister` is not (irreversible).

**`backend/app/connectors/catalog/aws.json`** — two new catalog actions:

`ecs_rolling_deploy`:
- execution_tier: 2
- estimated_duration_seconds: 300
- rollback_strategy: `"reconstitution"`
- applicable_asset_types: `["cloud_account"]`
- parameters: all parameters from the table above

`ecs_task_def_deregister`:
- execution_tier: 2
- estimated_duration_seconds: 30
- rollback_strategy: `"no_op"`
- applicable_asset_types: `["cloud_account"]`
- parameters: `task_def_arn`, `region`

**`backend/app/connectors/change_type_definitions/`** — two new single-step JSON files:
- `ecs_rolling_deploy.json`
- `ecs_task_def_deregister.json`

## Smoke Test

File: `backend/tests/smoke/test_ecs_rolling_deploy_smoke.py`  
Class: `TestEcsRollingDeploySmoke`

**Setup**: Create Fargate cluster + task def (`nginx:alpine`, single container, env var `SMOKE_ENV=original`) + service (desired count 1, deployment circuit breaker disabled so Nexplane controls rollback). Wait for service to reach `runningCount == 1`.

**Phase 1 — successful deploy + manual rollback:**
1. Run `ecs_rolling_deploy` CR with `env_var_overrides=[{key: SMOKE_ENV, old_value: original, new_value: updated}]` and `image_tag=nginx:1.27`.
2. Poll until CR `completed`.
3. Assert `execution_result["deployed"] == True`, `rolled_back == False`.
4. Assert `new_task_def_arn` differs from `old_task_def_arn`.
5. Assert ECS service `taskDefinition` field == `new_task_def_arn`.
6. Trigger platform rollback via `POST /change-requests/{id}/rollback`.
7. Assert service `taskDefinition` field == `old_task_def_arn`.

**Phase 2 — auto-rollback on bad image:**
1. Run `ecs_rolling_deploy` CR with `image_tag=nginx:this-tag-does-not-exist-99999`, `stability_timeout_seconds=120`.
2. Poll until CR `completed`.
3. Assert `execution_result["rolled_back"] == True`.
4. Assert ECS service `taskDefinition` field == `old_task_def_arn` (service restored).

**Phase 3 — `ecs_task_def_deregister`:**
1. Run `ecs_task_def_deregister` CR with the bad-image task def ARN registered in Phase 2.
2. Assert `execution_result["deregistered"] == True`.
3. Assert `describe_task_definition` returns status `INACTIVE` for that ARN.

**Teardown**: `delete_service` → deregister all remaining task def revisions → `delete_cluster`.

Constants:
```python
SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"
SMOKE_ASSET_ID     = "1a7051be-7110-4a21-9cdf-b023231cdff8"
BASE_URL           = "http://localhost:8000"
CLUSTER_NAME       = "smoke-ecs-rolling-deploy"
SERVICE_NAME       = "smoke-ecs-svc"
TASK_FAMILY        = "smoke-ecs-task"
```

## Files Touched

**Create:**
- `backend/app/connectors/executors/aws/ecs_rolling_deploy.py`
- `backend/app/connectors/executors/aws/ecs_task_def_deregister.py`
- `backend/app/connectors/change_type_definitions/ecs_rolling_deploy.json`
- `backend/app/connectors/change_type_definitions/ecs_task_def_deregister.json`
- `backend/tests/smoke/test_ecs_rolling_deploy_smoke.py`

**Modify:**
- `backend/app/models/change_request.py` — add 2 ChangeType entries
- `backend/app/services/safety_engine.py` — add `ecs_rolling_deploy` to `_IMPLICIT_ROLLBACK_TYPES`
- `backend/app/connectors/catalog/aws.json` — add 2 catalog actions
