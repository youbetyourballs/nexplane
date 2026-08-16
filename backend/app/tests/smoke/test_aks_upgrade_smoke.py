# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""AKS cluster and node pool upgrade live smoke test.

Env vars required:
  SMOKE_AKS_RESOURCE_GROUP  — resource group name
  SMOKE_AKS_CLUSTER         — cluster name
  SMOKE_AKS_TARGET_VERSION  — target k8s version (e.g. '1.28.5')
  SMOKE_AKS_NODE_POOL       — node pool name (e.g. 'nodepool1')

Azure credentials via:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_SUBSCRIPTION_ID
"""
import os
import pytest
from app.connectors.executors.azure.aks_cluster_upgrade import execute as cluster_upgrade, rollback as cluster_rollback
from app.connectors.executors.azure.aks_node_pool_upgrade import execute as pool_upgrade, rollback as pool_rollback


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_AKS_CLUSTER"), reason="SMOKE_AKS_CLUSTER not set")
async def test_aks_cluster_upgrade(live_azure_connector):
    """Test AKS control plane upgrade — intentionally irreversible."""
    params = {
        "resource_group": os.environ["SMOKE_AKS_RESOURCE_GROUP"],
        "cluster_name": os.environ["SMOKE_AKS_CLUSTER"],
        "target_version": os.environ["SMOKE_AKS_TARGET_VERSION"],
    }
    result = await cluster_upgrade(params, [], live_azure_connector)
    assert result["status"] in ("upgraded", "already_at_version")
    # Control plane rollback is intentionally irreversible — verify rollback returns partial
    rb = await cluster_rollback(params, result, live_azure_connector)
    assert rb["rolled_back"] is False
    assert "irreversible" in rb["reason"].lower()


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_AKS_NODE_POOL"), reason="SMOKE_AKS_NODE_POOL not set")
async def test_aks_node_pool_upgrade_and_rollback(live_azure_connector):
    """Test AKS node pool upgrade with full rollback support."""
    params = {
        "resource_group": os.environ["SMOKE_AKS_RESOURCE_GROUP"],
        "cluster_name": os.environ["SMOKE_AKS_CLUSTER"],
        "node_pool_name": os.environ["SMOKE_AKS_NODE_POOL"],
        "target_version": os.environ["SMOKE_AKS_TARGET_VERSION"],
    }
    result = await pool_upgrade(params, [], live_azure_connector)
    assert result["status"] in ("upgraded", "already_at_version")

    if result["status"] == "upgraded":
        rb = await pool_rollback(params, result, live_azure_connector)
        assert rb["rolled_back"] is True
