# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/aks_cluster_upgrade executor.

Requires: AKS cluster at version 1.28 (provision first, takes ~5 min).

Set env vars:
  SMOKE_AKS_RESOURCE_GROUP=<resource-group>
  SMOKE_AKS_CLUSTER_NAME=<cluster-name>
  SMOKE_AKS_TARGET_VERSION=1.29  (optional)

Run:
  SMOKE_AKS_RESOURCE_GROUP=... SMOKE_AKS_CLUSTER_NAME=... \
  pytest app/tests/executors/test_aks_cluster_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_aks_cluster_upgrade.log when complete.

NOTE: AKS control plane upgrades are irreversible (cannot downgrade).
Phase 2 verifies that rollback returns rolled_back: False with documented reason.
NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
Do NOT delete the AKS cluster after this test — it is reused for node pool upgrade smoke.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

RESOURCE_GROUP = os.environ.get("SMOKE_AKS_RESOURCE_GROUP", "")
CLUSTER_NAME = os.environ.get("SMOKE_AKS_CLUSTER_NAME", "")
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
        return c


@pytest.mark.asyncio
async def test_aks_cluster_upgrade_all_phases():
    from app.connectors.executors.azure.aks_cluster_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade control plane 1.28→1.29
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade AKS cluster to %s ===", TARGET_VERSION)
    result1 = await execute(
        {
            "resource_group": RESOURCE_GROUP,
            "cluster_name": CLUSTER_NAME,
            "target_version": TARGET_VERSION,
        },
        [],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_aks_cluster_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: upgraded to {TARGET_VERSION}, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (irreversible — AKS cannot downgrade control plane)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (irreversible) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is False, f"Expected irreversible rollback response: {result2}"
    assert "reason" in result2, f"Missing reason in rollback response: {result2}"
    with open("/tmp/smoke_aks_cluster_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: irreversible documented, reason={result2['reason']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
