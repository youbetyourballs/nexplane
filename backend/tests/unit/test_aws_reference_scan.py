# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_boto(lambda_env_vars):
    """Return a boto3 client mock that returns given Lambda env vars."""
    mock_lambda = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Functions": [
            {
                "FunctionArn": "arn:aws:lambda:us-east-1:123:function:my-func",
                "FunctionName": "my-func",
                "Environment": {"Variables": lambda_env_vars},
            }
        ]}
    ]
    mock_lambda.get_paginator.return_value = mock_paginator
    return mock_lambda


@pytest.mark.asyncio
async def test_scan_lambda_finds_matching_term():
    from app.connectors.executors.aws.reference_scan import scan_lambda_env_vars

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    with patch("boto3.client", return_value=_make_mock_boto({"DB_HOST": "old-db.internal", "OTHER": "val"})):
        result = await scan_lambda_env_vars(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "aws_lambda_env"
    assert hit["matched_term"] == "old-db.internal"
    assert "DB_HOST" in hit["snippet"]
    assert hit["consumer_identity"]["stable_id"] == "arn:aws:lambda:us-east-1:123:function:my-func"


@pytest.mark.asyncio
async def test_scan_lambda_no_match():
    from app.connectors.executors.aws.reference_scan import scan_lambda_env_vars

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["does-not-exist"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    with patch("boto3.client", return_value=_make_mock_boto({"DB_HOST": "other-host"})):
        result = await scan_lambda_env_vars(mock_cr, mock_connector, mock_db)

    assert result["hits"] == []
    assert result["scan_summary"]["matched"] == 0


@pytest.mark.asyncio
async def test_scan_lambda_scan_summary_counts():
    from app.connectors.executors.aws.reference_scan import scan_lambda_env_vars

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["target"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_lambda = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Functions": [
            {"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn1", "FunctionName": "fn1",
             "Environment": {"Variables": {"DB_HOST": "target.internal"}}},
            {"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn2", "FunctionName": "fn2",
             "Environment": {"Variables": {"DB_HOST": "other.internal"}}},
        ]}
    ]
    mock_lambda.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_lambda):
        result = await scan_lambda_env_vars(mock_cr, mock_connector, mock_db)

    assert result["scan_summary"]["scanned"] == 2
    assert result["scan_summary"]["matched"] == 1
    assert len(result["hits"]) == 1


@pytest.mark.asyncio
async def test_scan_ecs_task_defs_finds_env_var():
    from app.connectors.executors.aws.reference_scan import scan_ecs_task_defs

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_paginator = MagicMock()
    task_arn = "arn:aws:ecs:us-east-1:123:task-definition/myapp:1"
    mock_paginator.paginate.return_value = [{"taskDefinitionArns": [task_arn]}]
    mock_ecs.get_paginator.return_value = mock_paginator
    mock_ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "taskDefinitionArn": task_arn,
            "containerDefinitions": [
                {
                    "name": "app",
                    "environment": [{"name": "DB_HOST", "value": "old-db.internal"}],
                    "secrets": [],
                }
            ],
        }
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await scan_ecs_task_defs(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "aws_ecs_task_def"
    assert hit["matched_term"] == "old-db.internal"
    assert "DB_HOST" in hit["snippet"]
    assert hit["consumer_identity"]["stable_id"] == task_arn


@pytest.mark.asyncio
async def test_scan_ecs_task_defs_finds_secret_ref():
    from app.connectors.executors.aws.reference_scan import scan_ecs_task_defs

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["arn:aws:secretsmanager:us-east-1:123:secret:old"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ecs = MagicMock()
    mock_paginator = MagicMock()
    task_arn = "arn:aws:ecs:us-east-1:123:task-definition/myapp:1"
    mock_paginator.paginate.return_value = [{"taskDefinitionArns": [task_arn]}]
    mock_ecs.get_paginator.return_value = mock_paginator
    mock_ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "taskDefinitionArn": task_arn,
            "containerDefinitions": [
                {
                    "name": "app",
                    "environment": [],
                    "secrets": [
                        {"name": "DB_PASS", "valueFrom": "arn:aws:secretsmanager:us-east-1:123:secret:old-db-pass"},
                    ],
                }
            ],
        }
    }

    with patch("boto3.client", return_value=mock_ecs):
        result = await scan_ecs_task_defs(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "aws_ecs_task_def"


@pytest.mark.asyncio
async def test_scan_rds_parameter_groups_finds_match():
    from app.connectors.executors.aws.reference_scan import scan_rds_parameter_groups

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    pg_arn = "arn:aws:rds:us-east-1:123:pg:myparamgroup"

    mock_rds = MagicMock()

    def get_paginator(name):
        p = MagicMock()
        if name == "describe_db_parameter_groups":
            p.paginate.return_value = [{"DBParameterGroups": [
                {"DBParameterGroupName": "myparamgroup", "DBParameterGroupArn": pg_arn}
            ]}]
        elif name == "describe_db_parameters":
            p.paginate.return_value = [{"Parameters": [
                {"ParameterName": "init_connect", "ParameterValue": "SET search_path=old-db.internal"}
            ]}]
        return p

    mock_rds.get_paginator.side_effect = get_paginator

    with patch("boto3.client", return_value=mock_rds):
        result = await scan_rds_parameter_groups(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "aws_rds_parameter_group"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == pg_arn


@pytest.mark.asyncio
async def test_scan_secrets_manager_metadata_name_match():
    from app.connectors.executors.aws.reference_scan import scan_secrets_manager_metadata

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    secret_arn = "arn:aws:secretsmanager:us-east-1:123:secret:old-db-creds"
    mock_sm = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"SecretList": [
        {"ARN": secret_arn, "Name": "old-db-creds", "Description": "Credentials for old database", "Tags": []}
    ]}]
    mock_sm.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_sm):
        result = await scan_secrets_manager_metadata(mock_cr, mock_connector, mock_db)

    # Hits on name ("old-db-creds" contains "old-db"); description does not contain "old-db"
    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "aws_secrets_manager_metadata"
    assert result["hits"][0]["consumer_identity"]["stable_id"] == secret_arn
    assert result["hits"][0]["snippet"] == "name: old-db-creds"


@pytest.mark.asyncio
async def test_scan_secrets_manager_metadata_tag_match():
    from app.connectors.executors.aws.reference_scan import scan_secrets_manager_metadata

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["prod-db"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    secret_arn = "arn:aws:secretsmanager:us-east-1:123:secret:some-secret"
    mock_sm = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"SecretList": [
        {"ARN": secret_arn, "Name": "some-secret", "Description": "", "Tags": [
            {"Key": "Database", "Value": "prod-db.internal"}
        ]}
    ]}]
    mock_sm.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_sm):
        result = await scan_secrets_manager_metadata(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "aws_secrets_manager_metadata"


@pytest.mark.asyncio
async def test_scan_ssm_parameters_metadata_name_match():
    from app.connectors.executors.aws.reference_scan import scan_ssm_parameters_metadata

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ssm = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Parameters": [
        {"Name": "/prod/old-db/host", "Description": "Hostname of old database"},
        {"Name": "/prod/other/host", "Description": "Unrelated param"},
    ]}]
    mock_ssm.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_ssm):
        result = await scan_ssm_parameters_metadata(mock_cr, mock_connector, mock_db)

    # /prod/old-db/host matches on name only (description "Hostname of old database" lacks "old-db")
    # /prod/other/host has no match
    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "aws_ssm_parameter_metadata"
    assert result["hits"][0]["consumer_identity"]["stable_id"] == "/prod/old-db/host"
    assert result["hits"][0]["snippet"] == "name: /prod/old-db/host"


@pytest.mark.asyncio
async def test_scan_ssm_parameters_metadata_no_values_scanned():
    """Confirms SSM scan never touches parameter values — only names and descriptions."""
    from app.connectors.executors.aws.reference_scan import scan_ssm_parameters_metadata

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["secret-value"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ssm = MagicMock()
    mock_paginator = MagicMock()
    # Parameter value contains the term but name/description do not
    mock_paginator.paginate.return_value = [{"Parameters": [
        {"Name": "/prod/db/password", "Description": "DB password", "Value": "secret-value"},
    ]}]
    mock_ssm.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_ssm):
        result = await scan_ssm_parameters_metadata(mock_cr, mock_connector, mock_db)

    # Should NOT match because we only scan name/description, not Value
    assert result["hits"] == []
    assert result["scan_summary"]["matched"] == 0


@pytest.mark.asyncio
async def test_scan_ec2_user_data_finds_term():
    from app.connectors.executors.aws.reference_scan import scan_ec2_user_data

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    instance_id = "i-0abc123456"
    user_data_script = "#!/bin/bash\nexport DB_HOST=old-db.internal\necho done"
    encoded = base64.b64encode(user_data_script.encode()).decode()

    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Reservations": [
        {"Instances": [
            {"InstanceId": instance_id, "State": {"Name": "running"}}
        ]}
    ]}]
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_ec2.describe_instance_attribute.return_value = {
        "UserData": {"Value": encoded}
    }

    with patch("boto3.client", return_value=mock_ec2):
        result = await scan_ec2_user_data(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "aws_ec2_user_data"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == instance_id
    assert "old-db.internal" in hit["snippet"]


@pytest.mark.asyncio
async def test_scan_ec2_user_data_skips_stopped_instances():
    from app.connectors.executors.aws.reference_scan import scan_ec2_user_data

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Reservations": [
        {"Instances": [
            {"InstanceId": "i-stopped", "State": {"Name": "stopped"}}
        ]}
    ]}]
    mock_ec2.get_paginator.return_value = mock_paginator

    with patch("boto3.client", return_value=mock_ec2):
        result = await scan_ec2_user_data(mock_cr, mock_connector, mock_db)

    assert result["hits"] == []
    assert result["scan_summary"]["scanned"] == 0
    mock_ec2.describe_instance_attribute.assert_not_called()
