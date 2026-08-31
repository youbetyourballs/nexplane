# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for oci/oci_cache_upgrade executor.

Requires: OCI Redis cluster at version 6.x (provision first).

Set env vars:
  SMOKE_OCI_CACHE_CLUSTER_ID=<cluster-ocid>
  SMOKE_OCI_CACHE_TARGET_VERSION=7.0.5  (optional)

Run:
  SMOKE_OCI_CACHE_CLUSTER_ID=... \
  pytest app/tests/executors/test_oci_cache_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_oci_cache_upgrade.log when complete.

NOTE: OCI Redis version downgrades are not supported — rollback returns rolled_back: False.
Phase 2 verifies the partial rollback response is well-formed.
NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

CLUSTER_ID = os.environ.get("SMOKE_OCI_CACHE_CLUSTER_ID", "")
TARGET_VERSION = os.environ.get("SMOKE_OCI_CACHE_TARGET_VERSION", "7.0.5")

pytestmark = pytest.mark.skipif(
    not CLUSTER_ID,
    reason="Set SMOKE_OCI_CACHE_CLUSTER_ID to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "oci").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No OCI connector found in platform")
        from app.services.connector_service import _attach_credentials
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_oci_cache_upgrade_all_phases():
    from app.connectors.executors.oci.oci_cache_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade cache
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade cache to %s ===", TARGET_VERSION)
    result1 = await execute(
        {"cluster_id": CLUSTER_ID, "target_version": TARGET_VERSION},
        [],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_oci_cache_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: upgraded to {TARGET_VERSION}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (partial — OCI Redis cannot downgrade)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (partial — irreversible) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is False, f"Expected partial rollback response: {result2}"
    assert "reason" in result2, f"Missing reason in rollback response: {result2}"
    with open("/tmp/smoke_oci_cache_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: partial documented, reason={result2['reason']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
