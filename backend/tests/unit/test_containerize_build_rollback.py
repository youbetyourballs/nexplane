# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_rollback_dispatches_agent_job():
    execution_result = {
        "image_name": "ghcr.io/org/myapp",
        "image_digest": "sha256:abc123",
        "asset_ids": ["asset-uuid-1"],
    }
    mock_dispatch = AsyncMock(return_value={"deleted": True})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        from app.connectors.executors.nexplane_agent.containerize_build import rollback
        result = await rollback({}, execution_result, None)
    assert result["rolled_back"] is True
    mock_dispatch.assert_called_once_with(
        command="containerize_build_rollback",
        parameters={"image_name": "ghcr.io/org/myapp", "image_digest": "sha256:abc123"},
        asset_ids=["asset-uuid-1"],
        timeout_seconds=60,
    )


@pytest.mark.asyncio
async def test_rollback_no_coordinates():
    from app.connectors.executors.nexplane_agent.containerize_build import rollback
    result = await rollback({}, {}, None)
    assert result["rolled_back"] is False
    assert result["reason"] == "no_image_coordinates"


@pytest.mark.asyncio
async def test_rollback_agent_returns_false():
    execution_result = {
        "image_name": "ghcr.io/org/myapp",
        "image_digest": "sha256:abc123",
    }
    mock_dispatch = AsyncMock(return_value={"deleted": False, "error": "not found"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        from app.connectors.executors.nexplane_agent.containerize_build import rollback
        result = await rollback({}, execution_result, None)
    assert result["rolled_back"] is False
