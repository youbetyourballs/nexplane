# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_update_lambda_env_var_saves_rollback_data():
    from app.connectors.executors.aws.reference_update import update_lambda_env_var

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "function_arn": "arn:aws:lambda:us-east-1:123:function:api-gw",
        "env_var_key": "DB_HOST",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_lambda = MagicMock()
    mock_lambda.get_function_configuration.return_value = {
        "Environment": {"Variables": {"DB_HOST": "old-db.internal", "OTHER": "x"}}
    }
    mock_lambda.update_function_configuration.return_value = {}

    with patch("boto3.client", return_value=mock_lambda):
        result = await update_lambda_env_var(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["rollback_data"]["old_value"] == "old-db.internal"
    mock_lambda.update_function_configuration.assert_called_once()
    call_kwargs = mock_lambda.update_function_configuration.call_args[1]
    assert call_kwargs["Environment"]["Variables"]["DB_HOST"] == "new-db.internal"
    assert call_kwargs["Environment"]["Variables"]["OTHER"] == "x"


@pytest.mark.asyncio
async def test_update_lambda_env_var_skips_if_mismatch():
    from app.connectors.executors.aws.reference_update import update_lambda_env_var

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "function_arn": "arn:aws:lambda:us-east-1:123:function:api-gw",
        "env_var_key": "DB_HOST",
        "old_value": "expected-old.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_lambda = MagicMock()
    mock_lambda.get_function_configuration.return_value = {
        "Environment": {"Variables": {"DB_HOST": "actual-current.internal"}}
    }

    with patch("boto3.client", return_value=mock_lambda):
        result = await update_lambda_env_var(mock_cr, mock_connector, mock_db)

    assert result["status"] == "skipped"
    mock_lambda.update_function_configuration.assert_not_called()


@pytest.mark.asyncio
async def test_update_ecs_task_def_env_saves_rollback_data():
    from app.connectors.executors.aws.reference_update import update_ecs_task_def_env

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "task_def_arn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:5",
        "container_name": "app",
        "env_var_key": "DB_HOST",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "family": "myapp",
            "containerDefinitions": [
                {
                    "name": "app",
                    "environment": [
                        {"name": "DB_HOST", "value": "old-db.internal"},
                        {"name": "PORT", "value": "5432"},
                    ],
                }
            ],
        }
    }
    mock_ecs.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"}
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await update_ecs_task_def_env(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["new_task_def_arn"] == "arn:aws:ecs:us-east-1:123:task-definition/myapp:6"
    assert result["rollback_data"]["old_task_def_arn"] == "arn:aws:ecs:us-east-1:123:task-definition/myapp:5"


@pytest.mark.asyncio
async def test_update_ecs_task_def_env_skips_if_not_found():
    from app.connectors.executors.aws.reference_update import update_ecs_task_def_env

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "task_def_arn": "arn:aws:ecs:us-east-1:123:task-definition/myapp:5",
        "container_name": "app",
        "env_var_key": "DB_HOST",
        "old_value": "wrong-old.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "family": "myapp",
            "containerDefinitions": [
                {
                    "name": "app",
                    "environment": [{"name": "DB_HOST", "value": "actual-current.internal"}],
                }
            ],
        }
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await update_ecs_task_def_env(mock_cr, mock_connector, mock_db)

    assert result["status"] == "skipped"
    mock_ecs.register_task_definition.assert_not_called()


@pytest.mark.asyncio
async def test_update_ssm_parameter_value_saves_rollback_data():
    from app.connectors.executors.aws.reference_update import update_ssm_parameter_value

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "parameter_name": "/myapp/db_host",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Name": "/myapp/db_host", "Value": "old-db.internal", "Type": "String"}
    }
    mock_ssm.put_parameter.return_value = {}

    with patch("boto3.client", return_value=mock_ssm):
        result = await update_ssm_parameter_value(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["rollback_data"]["old_value"] == "old-db.internal"
    mock_ssm.put_parameter.assert_called_once()


@pytest.mark.asyncio
async def test_update_ssm_parameter_value_skips_if_mismatch():
    from app.connectors.executors.aws.reference_update import update_ssm_parameter_value

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "parameter_name": "/myapp/db_host",
        "old_value": "expected-old.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Name": "/myapp/db_host", "Value": "actual-current.internal", "Type": "String"}
    }

    with patch("boto3.client", return_value=mock_ssm):
        result = await update_ssm_parameter_value(mock_cr, mock_connector, mock_db)

    assert result["status"] == "skipped"
    mock_ssm.put_parameter.assert_not_called()


@pytest.mark.asyncio
async def test_update_ssm_parameter_value_rejects_securestring():
    from app.connectors.executors.aws.reference_update import update_ssm_parameter_value

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "parameter_name": "/myapp/secret",
        "old_value": "old-secret",
        "new_value": "new-secret",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Name": "/myapp/secret", "Value": "old-secret", "Type": "SecureString"}
    }

    with patch("boto3.client", return_value=mock_ssm):
        result = await update_ssm_parameter_value(mock_cr, mock_connector, mock_db)

    assert result["status"] == "error"
    assert "SecureString" in result["reason"]
    mock_ssm.put_parameter.assert_not_called()


@pytest.mark.asyncio
async def test_update_secrets_manager_secret_description_saves_rollback_data():
    from app.connectors.executors.aws.reference_update import update_secrets_manager_secret_description

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123:secret:old-db-creds-abc123",
        "old_description": "Points to old-db.internal",
        "new_description": "Points to new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_sm = MagicMock()
    mock_sm.describe_secret.return_value = {"Description": "Points to old-db.internal"}
    mock_sm.update_secret.return_value = {}

    with patch("boto3.client", return_value=mock_sm):
        result = await update_secrets_manager_secret_description(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["rollback_data"]["old_description"] == "Points to old-db.internal"
    mock_sm.update_secret.assert_called_once()


@pytest.mark.asyncio
async def test_update_secrets_manager_secret_description_skips_if_mismatch():
    from app.connectors.executors.aws.reference_update import update_secrets_manager_secret_description

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123:secret:old-db-creds-abc123",
        "old_description": "expected old description",
        "new_description": "new description",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_sm = MagicMock()
    mock_sm.describe_secret.return_value = {"Description": "actual current description"}

    with patch("boto3.client", return_value=mock_sm):
        result = await update_secrets_manager_secret_description(mock_cr, mock_connector, mock_db)

    assert result["status"] == "skipped"
    mock_sm.update_secret.assert_not_called()
