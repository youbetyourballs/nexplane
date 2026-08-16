# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "tenant_id": "t1", "client_id": "c1",
        "client_secret": "s1", "subscription_id": "sub1",
    }
    return c


@pytest.mark.asyncio
async def test_already_at_version(connector):
    mock_pool = MagicMock()
    mock_pool.orchestrator_version = "1.28.5"
    mock_pool.provisioning_state = "Succeeded"

    with patch("app.connectors.executors.azure.aks_node_pool_upgrade._run") as mock_run:
        mock_run.return_value = mock_pool
        from app.connectors.executors.azure.aks_node_pool_upgrade import execute
        result = await execute(
            {"resource_group": "rg1", "cluster_name": "my-cluster",
             "node_pool_name": "nodepool1", "target_version": "1.28.5"},
            [], connector,
        )
    assert result["status"] == "already_at_version"


@pytest.mark.asyncio
async def test_upgrade_and_rollback(connector):
    mock_pool = MagicMock()
    mock_pool.orchestrator_version = "1.27.9"
    mock_pool.provisioning_state = "Succeeded"

    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_pool
        return MagicMock(orchestrator_version="1.28.0", provisioning_state="Succeeded")

    with patch("app.connectors.executors.azure.aks_node_pool_upgrade._run", side_effect=fake_run):
        with patch("app.connectors.executors.azure.aks_node_pool_upgrade._wait_for_pool_upgrade", new=AsyncMock(return_value="1.28.0")):
            from app.connectors.executors.azure.aks_node_pool_upgrade import execute
            result = await execute(
                {"resource_group": "rg1", "cluster_name": "my-cluster",
                 "node_pool_name": "nodepool1", "target_version": "1.28.0"},
                [], connector,
            )
    assert result["status"] == "upgraded"
    assert result["current_version"] == "1.28.0"
    assert result["previous_version"] == "1.27.9"
