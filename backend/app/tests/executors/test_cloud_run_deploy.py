# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"service_account_key_json": "{}", "project_id": "test-proj"}
    return c


@pytest.mark.asyncio
async def test_deploy_captures_previous_image(connector):
    mock_service = MagicMock()
    mock_service.template.containers = [MagicMock(image="gcr.io/proj/app:v1")]
    mock_service.uri = "https://app-xyz.run.app"

    call_count = [0]
    async def fake_run(fn):
        call_count[0] += 1
        # Call order: 1=get_project_id, 2=get_service, 3=update_service
        if call_count[0] == 2:
            return mock_service  # call 2: get_service returns the service with v1 image
        return MagicMock()  # calls 1 & 3: get_project_id and update_service

    with patch("app.connectors.executors.gcp.cloud_run_deploy._run", side_effect=fake_run):
        with patch("app.connectors.executors.gcp.cloud_run_deploy._wait_ready", new=AsyncMock(return_value="https://app-xyz.run.app")):
            from app.connectors.executors.gcp.cloud_run_deploy import execute
            result = await execute(
                {"service_name": "my-service", "region": "us-central1", "image": "gcr.io/proj/app:v2"},
                [], connector,
            )
    assert result["previous_image"] == "gcr.io/proj/app:v1"
    assert result["current_image"] == "gcr.io/proj/app:v2"
    assert result["status"] == "deployed"


@pytest.mark.asyncio
async def test_rollback_restores_image(connector):
    execution_result = {
        "service_name": "my-service",
        "region": "us-central1",
        "previous_image": "gcr.io/proj/app:v1",
        "current_image": "gcr.io/proj/app:v2",
        "url": "https://app-xyz.run.app",
    }
    with patch("app.connectors.executors.gcp.cloud_run_deploy._run", new=AsyncMock(return_value=MagicMock())):
        with patch("app.connectors.executors.gcp.cloud_run_deploy._wait_ready", new=AsyncMock(return_value="https://app-xyz.run.app")):
            from app.connectors.executors.gcp.cloud_run_deploy import rollback
            result = await rollback({}, execution_result, connector)
    assert result["rolled_back"] is True
    assert result["restored_image"] == "gcr.io/proj/app:v1"
