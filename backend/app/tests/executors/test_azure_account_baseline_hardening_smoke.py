# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for azure/azure_account_baseline_hardening executor.

No extra env vars beyond Azure connector credentials.
Set SMOKE_AZURE_BASELINE_HARDENING=1 to opt in (applies subscription-level policy assignments).

Run:
  SMOKE_AZURE_BASELINE_HARDENING=1 \
  pytest app/tests/executors/test_azure_account_baseline_hardening_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_azure_account_baseline_hardening.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

SMOKE_ENABLED = os.environ.get("SMOKE_AZURE_BASELINE_HARDENING", "")

pytestmark = pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="Set SMOKE_AZURE_BASELINE_HARDENING=1 to run live smoke test",
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
async def test_azure_account_baseline_hardening_all_phases():
    from app.connectors.executors.azure.azure_account_baseline_hardening import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Apply CIS baseline policies
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Apply CIS baseline policies ===")
    result1 = await execute({}, [], connector)
    logger.info("Hardening result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_azure_account_baseline_hardening.log", "a") as f:
        f.write(f"Phase 1 PASS: baseline applied, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (delete policy assignments)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (delete policy assignments) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_azure_account_baseline_hardening.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
