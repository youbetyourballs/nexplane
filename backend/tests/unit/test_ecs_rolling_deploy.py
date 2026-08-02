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
