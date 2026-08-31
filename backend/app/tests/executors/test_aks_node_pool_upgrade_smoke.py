# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/aks_node_pool_upgrade executor.

Requires: Same AKS cluster used for aks_cluster_upgrade smoke (already at 1.29).

Set env vars:
  SMOKE_AKS_RESOURCE_GROUP=<resource-group>
  SMOKE_AKS_CLUSTER_NAME=<cluster-name>
  SMOKE_AKS_NODE_POOL_NAME=nodepool1  (optional)
  SMOKE_AKS_TARGET_VERSION=1.29  (optional)

Run:
  SMOKE_AKS_RESOURCE_GROUP=... SMOKE_AKS_CLUSTER_NAME=... \
  pytest app/tests/executors/test_aks_node_pool_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_aks_node_pool_upgrade.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
Delete the AKS cluster after this test completes.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

RESOURCE_GROUP = os.environ.get("SMOKE_AKS_RESOURCE_GROUP", "")
CLUSTER_NAME = os.environ.get("SMOKE_AKS_CLUSTER_NAME", "")
NODE_POOL_NAME = os.environ.get("SMOKE_AKS_NODE_POOL_NAME", "nodepool1")
TARGET_VERSION = os.environ.get("SMOKE_AKS_TARGET_VERSION", "1.29")

pytestmark = pytest.mark.skipif(
    not RESOURCE_GROUP or not CLUSTER_NAME,
    reason="Set SMOKE_AKS_RESOURCE_GROUP and SMOKE_AKS_CLUSTER_NAME to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "azure").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No Azure connector found in platform")
        from app.services.connector_service import _attach_credentials
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_aks_node_pool_upgrade_all_phases():
    from app.connectors.executors.azure.aks_node_pool_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade node pool
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade node pool %s to %s ===", NODE_POOL_NAME, TARGET_VERSION)
    result1 = await execute(
        {
            "resource_group": RESOURCE_GROUP,
            "cluster_name": CLUSTER_NAME,
            "node_pool_name": NODE_POOL_NAME,
            "target_version": TARGET_VERSION,
        },
        [],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_aks_node_pool_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: node pool upgraded to {TARGET_VERSION}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2.get("rolled_back") is not None, f"Phase 2 missing rolled_back field: {result2}"
    with open("/tmp/smoke_aks_node_pool_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: rolled_back={result2.get('rolled_back')}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
