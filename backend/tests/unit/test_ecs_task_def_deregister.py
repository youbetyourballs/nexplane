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
    mock_ecs = MagicMock()
    mock_ecs.deregister_task_definition.return_value = {
        "taskDefinition": {"status": "INACTIVE"}
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await execute(mock_cr.parameters, [], mock_connector)

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

    mock_ecs = MagicMock()
    mock_ecs.deregister_task_definition.return_value = {"taskDefinition": {"status": "INACTIVE"}}

    with patch("boto3.client", return_value=mock_ecs) as mock_boto:
        await execute(mock_cr.parameters, [], mock_connector)

    # boto3.client was called with region_name="eu-west-1" (from params, overrides credential default)
    call_kwargs = mock_boto.call_args[1]
    assert call_kwargs["region_name"] == "eu-west-1"


def test_rollback_capability_is_irreversible():
    import importlib
    mod = importlib.import_module("app.connectors.executors.aws.ecs_task_def_deregister")
    assert mod.ROLLBACK_CAPABILITY == "irreversible"
    assert hasattr(mod, "ROLLBACK_REASON")
