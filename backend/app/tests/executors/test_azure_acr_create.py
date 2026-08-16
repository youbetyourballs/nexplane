# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class ResourceExistsError(Exception):
    """Stub for azure.core.exceptions.ResourceExistsError — avoids SDK import in unit tests."""


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "tenant_id": "t1", "client_id": "c1",
        "client_secret": "s1", "subscription_id": "sub1",
    }
    return c


@pytest.mark.asyncio
async def test_creates_registry(connector):
    mock_registry = MagicMock()
    mock_registry.name = "myregistry"
    mock_registry.login_server = "myregistry.azurecr.io"
    mock_registry.location = "eastus"

    with patch("app.connectors.executors.azure.azure_acr_create._run",
               new=AsyncMock(return_value=mock_registry)):
        from app.connectors.executors.azure.azure_acr_create import execute
        result = await execute(
            {"resource_group": "rg1", "registry_name": "myregistry", "location": "eastus"},
            [], connector,
        )
    assert result["status"] == "created"
    assert result["login_server"] == "myregistry.azurecr.io"


@pytest.mark.asyncio
async def test_already_exists_returns_no_op(connector):
    mock_existing = MagicMock()
    mock_existing.name = "myregistry"
    mock_existing.login_server = "myregistry.azurecr.io"
    mock_existing.location = "eastus"

    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ResourceExistsError("registry already exists")
        return mock_existing

    with patch("app.connectors.executors.azure.azure_acr_create._run", side_effect=fake_run):
        from app.connectors.executors.azure.azure_acr_create import execute
        result = await execute(
            {"resource_group": "rg1", "registry_name": "myregistry", "location": "eastus"},
            [], connector,
        )
    assert result["already_exists"] is True


@pytest.mark.asyncio
async def test_rollback_deletes_registry(connector):
    execution_result = {
        "registry_name": "myregistry",
        "resource_group": "rg1",
        "already_exists": False,
    }
    with patch("app.connectors.executors.azure.azure_acr_create._run",
               new=AsyncMock(return_value=None)):
        from app.connectors.executors.azure.azure_acr_create import rollback
        result = await rollback({}, execution_result, connector)
    assert result["rolled_back"] is True
