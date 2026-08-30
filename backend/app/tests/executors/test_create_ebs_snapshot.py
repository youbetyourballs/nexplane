# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_execute_calls_real_when_creds_resolved_from_db():
    """When connector=None, executor should call _get_aws_creds and use real EC2 API."""
    fake_creds = {"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "s3cr3t", "region": "us-east-1"}
    fake_snap = {"SnapshotId": "snap-0abc1234", "State": "pending"}

    with patch(
        "app.connectors.executors.aws.create_ebs_snapshot._get_aws_creds_for_snapshot",
        new=AsyncMock(return_value=fake_creds),
    ), patch(
        "app.connectors.executors.aws.create_ebs_snapshot._get_ec2_client",
        return_value=MagicMock(
            create_snapshot=MagicMock(return_value=fake_snap),
            describe_instances=MagicMock(return_value={
                "Reservations": [{"Instances": [{"BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-111"}}]}]}]
            }),
        ),
    ):
        from app.connectors.executors.aws.create_ebs_snapshot import execute
        result = await execute(
            {"instance_id": "i-001", "description": "pre-change-abc"},
            ["asset-1"],
            None,  # connector=None — must NOT return mock
        )
    assert result.get("mock") is not True
    assert result["snapshot_id"] == "snap-0abc1234"


@pytest.mark.asyncio
async def test_execute_returns_mock_when_no_creds_anywhere():
    """When connector=None AND DB has no AWS creds, return mock."""
    with patch(
        "app.connectors.executors.aws.create_ebs_snapshot._get_aws_creds_for_snapshot",
        new=AsyncMock(return_value={}),
    ):
        from app.connectors.executors.aws.create_ebs_snapshot import execute
        result = await execute({"description": "pre-change-abc"}, ["asset-1"], None)
    assert result["mock"] is True
    assert result["snapshot_id"] == "snap-mock-0000"
