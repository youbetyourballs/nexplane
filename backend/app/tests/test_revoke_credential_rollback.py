# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def _make_connector(creds: dict):
    c = MagicMock()
    c.creds = creds
    return c


@pytest.mark.asyncio
async def test_aws_iam_key_execute_captures_rollback_params():
    """Execute must save username + user_arn into rollback_params before deleting the key."""
    fake_creds = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
        "region": "us-east-1",
    }
    connector = _make_connector(fake_creds)

    mock_iam = MagicMock()
    mock_iam.get_access_key_last_used.return_value = {"UserName": "smoke-user"}
    mock_iam.get_user.return_value = {"User": {"Arn": "arn:aws:iam::123:user/smoke-user"}}
    mock_iam.delete_access_key.return_value = {}

    with patch("boto3.client", return_value=mock_iam):
        from app.connectors.executors.aws.revoke_exposed_credential import execute
        result = await execute(
            {"credential_type": "aws_iam_key", "credential_id": "AKIACOMPROMISED"},
            [],
            connector,
        )

    assert result["success"] is True
    assert result["rollback_available"] is True
    assert result["rollback_type"] == "reconstitution"
    rp = result["rollback_params"]
    assert rp["username"] == "smoke-user"
    assert rp["user_arn"] == "arn:aws:iam::123:user/smoke-user"
    assert rp["credential_type"] == "aws_iam_key"
    mock_iam.delete_access_key.assert_called_once_with(AccessKeyId="AKIACOMPROMISED")


@pytest.mark.asyncio
async def test_aws_iam_key_rollback_creates_new_key():
    """Rollback must call create_access_key and return the new key ID."""
    fake_creds = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
        "region": "us-east-1",
    }
    connector = _make_connector(fake_creds)

    mock_iam = MagicMock()
    mock_iam.create_access_key.return_value = {
        "AccessKey": {
            "AccessKeyId": "AKIANEWKEY",
            "SecretAccessKey": "newsecret",
            "UserName": "smoke-user",
        }
    }

    execution_result = {
        "success": True,
        "rollback_available": True,
        "rollback_type": "reconstitution",
        "rollback_params": {
            "credential_type": "aws_iam_key",
            "username": "smoke-user",
            "user_arn": "arn:aws:iam::123:user/smoke-user",
        },
    }

    with patch("boto3.client", return_value=mock_iam):
        from app.connectors.executors.aws.revoke_exposed_credential import rollback
        result = await rollback({}, execution_result, connector)

    assert result["rolled_back"] is True
    assert result["rollback_type"] == "reconstitution"
    assert result["new_access_key_id"] == "AKIANEWKEY"
    mock_iam.create_access_key.assert_called_once_with(UserName="smoke-user")


@pytest.mark.asyncio
async def test_other_credential_types_remain_no_rollback():
    """vault_token and other types must still return rollback_available: False."""
    connector = _make_connector(None)

    from app.connectors.executors.aws.revoke_exposed_credential import rollback
    result = await rollback({}, {"success": True, "rollback_available": False}, connector)
    assert result["rolled_back"] is False


@pytest.mark.asyncio
async def test_mock_mode_unchanged():
    """When no creds, mock mode must still work and not attempt rollback."""
    connector = _make_connector(None)

    from app.connectors.executors.aws.revoke_exposed_credential import execute
    result = await execute(
        {"credential_type": "aws_iam_key", "credential_id": "AKIAMOCK"},
        [],
        connector,
    )
    assert result["mock"] is True
    assert result["rolled_back_available"] is False
