# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for gcp/gcp_account_baseline_monitoring executor.

Enables Cloud Audit Logs and VPC Flow Logs at the project level, then rolls back.
No org-level access required (SCC and DNS logging are skipped_with_warning).

Set env vars:
  SMOKE_GCP_BASELINE_MONITORING=1  (explicit opt-in required)

Run:
  SMOKE_GCP_BASELINE_MONITORING=1 \
  pytest app/tests/executors/test_gcp_account_baseline_monitoring_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_gcp_account_baseline_monitoring.log when complete.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

SMOKE_ENABLED = os.environ.get("SMOKE_GCP_BASELINE_MONITORING", "")

pytestmark = pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="Set SMOKE_GCP_BASELINE_MONITORING=1 to run live smoke test",
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
        from app.services.connector_service import _attach_credentials
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_gcp_account_baseline_monitoring_all_phases():
    from app.connectors.executors.gcp.gcp_account_baseline_monitoring import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Enable baseline monitoring
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Enable GCP Account Baseline Monitoring ===")
    result1 = await execute({}, [], connector)
    logger.info("Enable result: %s", result1)
    assert "phases" in result1, f"Phase 1 failed: {result1}"
    assert "rollback_data" in result1, f"Missing rollback_data: {result1}"
    with open("/tmp/smoke_gcp_account_baseline_monitoring.log", "a") as f:
        f.write(f"Phase 1 PASS: baseline monitoring enabled, summary={result1.get('summary')}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2.get("rolled_back") is True, f"Phase 2 rollback failed: {result2}"
    with open("/tmp/smoke_gcp_account_baseline_monitoring.log", "a") as f:
        f.write(f"Phase 2 PASS: rolled_back={result2['rolled_back']}, undone={result2.get('undone')}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
