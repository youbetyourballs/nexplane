# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Container registry create/delete live smoke tests.

Env vars:
  SMOKE_GAR_LOCATION      — GCP region (e.g. 'us-central1')
  SMOKE_GAR_REPO_ID       — repository name to create/delete
  SMOKE_ACR_RG            — Azure resource group
  SMOKE_ACR_NAME          — registry name (globally unique)
  SMOKE_ACR_LOCATION      — Azure region (e.g. 'eastus')
"""
import os
import pytest
from app.connectors.executors.gcp.gcp_artifact_registry_create import execute as gar_execute, rollback as gar_rollback
from app.connectors.executors.azure.azure_acr_create import execute as acr_execute, rollback as acr_rollback


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_GAR_LOCATION")
    or not os.getenv("SMOKE_GAR_REPO_ID"),
    reason="SMOKE_GAR_LOCATION or SMOKE_GAR_REPO_ID not set",
)
async def test_gcp_artifact_registry_create_and_rollback(live_gcp_connector):
    params = {
        "location": os.environ["SMOKE_GAR_LOCATION"],
        "repository_id": os.environ["SMOKE_GAR_REPO_ID"],
        "format": "DOCKER",
    }
    result = await gar_execute(params, [], live_gcp_connector)
    assert result["status"] == "created"
    assert result["repository_name"]

    # Idempotency
    result2 = await gar_execute(params, [], live_gcp_connector)
    assert result2["already_exists"] is True

    rb = await gar_rollback(params, result, live_gcp_connector)
    assert rb["rolled_back"] is True


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_ACR_RG")
    or not os.getenv("SMOKE_ACR_NAME")
    or not os.getenv("SMOKE_ACR_LOCATION"),
    reason="SMOKE_ACR_RG, SMOKE_ACR_NAME, or SMOKE_ACR_LOCATION not set",
)
async def test_azure_acr_create_and_rollback(live_azure_connector):
    params = {
        "resource_group": os.environ["SMOKE_ACR_RG"],
        "registry_name": os.environ["SMOKE_ACR_NAME"],
        "location": os.environ["SMOKE_ACR_LOCATION"],
        "sku": "Basic",
    }
    result = await acr_execute(params, [], live_azure_connector)
    assert result["status"] == "created"
    assert result["login_server"]

    result2 = await acr_execute(params, [], live_azure_connector)
    assert result2["already_exists"] is True

    rb = await acr_rollback(params, result, live_azure_connector)
    assert rb["rolled_back"] is True
