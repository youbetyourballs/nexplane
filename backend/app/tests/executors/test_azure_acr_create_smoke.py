# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/azure_acr_create executor.

Set env vars:
  SMOKE_ACR_RESOURCE_GROUP=<resource-group>
  SMOKE_ACR_NAME=<registry-name>
  SMOKE_ACR_LOCATION=eastus  (optional)

Run:
  SMOKE_ACR_RESOURCE_GROUP=... SMOKE_ACR_NAME=... \
  pytest app/tests/executors/test_azure_acr_create_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_azure_acr_create.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

RESOURCE_GROUP = os.environ.get("SMOKE_ACR_RESOURCE_GROUP", "")
ACR_NAME = os.environ.get("SMOKE_ACR_NAME", "")
LOCATION = os.environ.get("SMOKE_ACR_LOCATION", "eastus")

pytestmark = pytest.mark.skipif(
    not RESOURCE_GROUP or not ACR_NAME,
    reason="Set SMOKE_ACR_RESOURCE_GROUP and SMOKE_ACR_NAME to run live smoke test",
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
            raise RuntimeError("No Azure connector found in platform — add one before running smoke")
        from app.services.connector_service import _attach_credentials
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_azure_acr_create_all_phases():
    from app.connectors.executors.azure.azure_acr_create import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Create registry
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Create registry ===")
    result1 = await execute(
        {
            "resource_group": RESOURCE_GROUP,
            "registry_name": ACR_NAME,
            "location": LOCATION,
            "sku": "Basic",
        },
        [],
        connector,
    )
    logger.info("Create result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_azure_acr_create.log", "a") as f:
        f.write(f"Phase 1 PASS: registry created, name={ACR_NAME}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (delete registry)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (delete registry) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_azure_acr_create.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
