# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/azure_cache_redis_upgrade executor.

Requires: Azure Cache for Redis instance at version 6 (provision first with
  az redis create --sku Basic --vm-size c0 --redis-version 6).

Set env vars:
  SMOKE_REDIS_RESOURCE_GROUP=<resource-group>
  SMOKE_REDIS_CACHE_NAME=<cache-name>
  SMOKE_REDIS_TARGET_VERSION=7  (optional, default 7)

Run:
  SMOKE_REDIS_RESOURCE_GROUP=... SMOKE_REDIS_CACHE_NAME=... \
  pytest app/tests/executors/test_azure_cache_redis_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_azure_cache_redis_upgrade.log when complete.

NOTE: Redis version downgrades are not supported — rollback returns rolled_back: False
with documented reason. Phase 2 verifies the partial rollback response is well-formed.
NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

RESOURCE_GROUP = os.environ.get("SMOKE_REDIS_RESOURCE_GROUP", "")
CACHE_NAME = os.environ.get("SMOKE_REDIS_CACHE_NAME", "")
TARGET_VERSION = os.environ.get("SMOKE_REDIS_TARGET_VERSION", "7")

pytestmark = pytest.mark.skipif(
    not RESOURCE_GROUP or not CACHE_NAME,
    reason="Set SMOKE_REDIS_RESOURCE_GROUP and SMOKE_REDIS_CACHE_NAME to run live smoke test",
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
async def test_azure_cache_redis_upgrade_all_phases():
    from app.connectors.executors.azure.azure_cache_redis_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade 6→7
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade Redis %s ===", TARGET_VERSION)
    result1 = await execute(
        {
            "resource_group": RESOURCE_GROUP,
            "cache_name": CACHE_NAME,
            "target_version": TARGET_VERSION,
        },
        [],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_azure_cache_redis_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: upgraded to {TARGET_VERSION}, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (partial — Azure Redis cannot downgrade)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (partial — irreversible) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is False, f"Expected partial rollback response: {result2}"
    assert "reason" in result2, f"Missing reason in rollback response: {result2}"
    with open("/tmp/smoke_azure_cache_redis_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: partial rollback documented, reason={result2['reason']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
