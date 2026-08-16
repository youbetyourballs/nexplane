# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from google.api_core.exceptions import AlreadyExists


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"service_account_key_json": "{}", "project_id": "test-proj"}
    return c


@pytest.mark.asyncio
async def test_creates_repository(connector):
    mock_repo = MagicMock()
    mock_repo.name = "projects/test-proj/locations/us-central1/repositories/my-repo"
    mock_repo.format_ = MagicMock(name="DOCKER")

    with patch("app.connectors.executors.gcp.gcp_artifact_registry_create._run",
               new=AsyncMock(return_value=mock_repo)):
        from app.connectors.executors.gcp.gcp_artifact_registry_create import execute
        result = await execute(
            {"location": "us-central1", "repository_id": "my-repo"},
            [], connector,
        )
    assert result["status"] == "created"
    assert result["repository_name"] == "projects/test-proj/locations/us-central1/repositories/my-repo"


@pytest.mark.asyncio
async def test_already_exists_returns_no_op(connector):
    async def fake_run(fn):
        from google.api_core.exceptions import AlreadyExists
        raise AlreadyExists("repository already exists")

    with patch("app.connectors.executors.gcp.gcp_artifact_registry_create._run", side_effect=fake_run):
        from app.connectors.executors.gcp.gcp_artifact_registry_create import execute
        result = await execute(
            {"location": "us-central1", "repository_id": "my-repo"},
            [], connector,
        )
    assert result["already_exists"] is True
    assert result["status"] == "created"


@pytest.mark.asyncio
async def test_rollback_deletes_repository(connector):
    execution_result = {
        "repository_name": "projects/test-proj/locations/us-central1/repositories/my-repo",
        "already_exists": False,
    }
    with patch("app.connectors.executors.gcp.gcp_artifact_registry_create._run",
               new=AsyncMock(return_value=None)):
        from app.connectors.executors.gcp.gcp_artifact_registry_create import rollback
        result = await rollback({}, execution_result, connector)
    assert result["rolled_back"] is True
