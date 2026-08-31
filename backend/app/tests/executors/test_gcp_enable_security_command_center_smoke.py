# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for gcp/enable_security_command_center executor.

IMPORTANT: Security Command Center enablement is IRREVERSIBLE at the org level.
Run this only once against the org. Rollback phase documents the irreversibility.

Set env vars:
  SMOKE_GCP_SCC=1  (explicit opt-in required)
  SMOKE_GCP_ORG_ID=<org-id>  (optional — auto-discovered from project's parent org)

Run:
  SMOKE_GCP_SCC=1 SMOKE_GCP_ORG_ID=... \
  pytest app/tests/executors/test_gcp_enable_security_command_center_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_enable_security_command_center.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

SMOKE_ENABLED = os.environ.get("SMOKE_GCP_SCC", "")
ORG_ID = os.environ.get("SMOKE_GCP_ORG_ID", "")

pytestmark = pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="Set SMOKE_GCP_SCC=1 to run live smoke test (irreversible org-level change)",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "gcp").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No GCP connector found in platform")
        return c


@pytest.mark.asyncio
async def test_gcp_enable_security_command_center_all_phases():
    from app.connectors.executors.gcp.enable_security_command_center import execute, rollback

    connector = await _load_connector()
    params = {}
    if ORG_ID:
        params["org_id"] = ORG_ID

    # ------------------------------------------------------------------
    # Phase 1: Enable Security Command Center
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Enable Security Command Center ===")
    result1 = await execute(params, [], connector)
    logger.info("Enable result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_enable_security_command_center.log", "a") as f:
        f.write(f"Phase 1 PASS: SCC enabled, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (irreversible — documents limitation)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (irreversible) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is False, f"Expected irreversible response: {result2}"
    assert "reason" in result2, f"Missing reason in rollback response: {result2}"
    with open("/tmp/smoke_enable_security_command_center.log", "a") as f:
        f.write(f"Phase 2 PASS: irreversible documented, reason={result2['reason']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
