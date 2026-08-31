# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/azure_database_upgrade executor.

Requires: Azure Database for PostgreSQL Flexible Server at version 14
  (provision first with az postgres flexible-server create --version 14).

Set env vars:
  SMOKE_AZDB_RESOURCE_GROUP=<resource-group>
  SMOKE_AZDB_SERVER_NAME=<server-name>
  SMOKE_AZDB_ENGINE=postgresql  (optional, default postgresql)
  SMOKE_AZDB_TARGET_VERSION=15  (optional, default 15)

Run:
  SMOKE_AZDB_RESOURCE_GROUP=... SMOKE_AZDB_SERVER_NAME=... \
  pytest app/tests/executors/test_azure_database_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_azure_database_upgrade.log when complete.

NOTE: Azure DB major version upgrades are irreversible — rollback returns rolled_back: False.
Phase 2 verifies the partial rollback response is well-formed.
NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

RESOURCE_GROUP = os.environ.get("SMOKE_AZDB_RESOURCE_GROUP", "")
SERVER_NAME = os.environ.get("SMOKE_AZDB_SERVER_NAME", "")
ENGINE = os.environ.get("SMOKE_AZDB_ENGINE", "postgresql")
TARGET_VERSION = os.environ.get("SMOKE_AZDB_TARGET_VERSION", "15")

pytestmark = pytest.mark.skipif(
    not RESOURCE_GROUP or not SERVER_NAME,
    reason="Set SMOKE_AZDB_RESOURCE_GROUP and SMOKE_AZDB_SERVER_NAME to run live smoke test",
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
async def test_azure_database_upgrade_all_phases():
    from app.connectors.executors.azure.azure_database_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade PostgreSQL 14→15
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade %s to %s ===", ENGINE, TARGET_VERSION)
    result1 = await execute(
        {
            "resource_group": RESOURCE_GROUP,
            "server_name": SERVER_NAME,
            "engine": ENGINE,
            "target_version": TARGET_VERSION,
        },
        [],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_azure_database_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: upgraded to {TARGET_VERSION}, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (partial — Azure DB major upgrades are irreversible)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (partial — irreversible) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is False, f"Expected partial rollback response: {result2}"
    assert "reason" in result2, f"Missing reason in rollback response: {result2}"
    with open("/tmp/smoke_azure_database_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: partial rollback documented, reason={result2['reason']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
