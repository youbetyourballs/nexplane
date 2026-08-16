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
async def test_deploy_captures_previous_image(connector):
    mock_app = MagicMock()
    mock_app.template.containers = [MagicMock(image="myregistry.azurecr.io/app:v1")]
    mock_app.configuration.ingress.fqdn = "app.eastus.azurecontainerapps.io"

    call_count = [0]
    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_app
        return MagicMock(
            template=MagicMock(containers=[MagicMock(image="myregistry.azurecr.io/app:v2")]),
            configuration=MagicMock(ingress=MagicMock(fqdn="app.eastus.azurecontainerapps.io")),
            provisioning_state="Succeeded",
        )

    with patch("app.connectors.executors.azure.azure_container_apps_deploy._run", side_effect=fake_run):
        with patch("app.connectors.executors.azure.azure_container_apps_deploy._wait_ready",
                   new=AsyncMock(return_value="app.eastus.azurecontainerapps.io")):
            from app.connectors.executors.azure.azure_container_apps_deploy import execute
            result = await execute(
                {"resource_group": "rg1", "app_name": "my-app", "image": "myregistry.azurecr.io/app:v2"},
                [], connector,
            )
    assert result["previous_image"] == "myregistry.azurecr.io/app:v1"
    assert result["status"] == "deployed"
