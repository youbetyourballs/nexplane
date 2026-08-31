# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for oci/oci_account_baseline_hardening executor.

Applies OCI IAM password policy hardening, then rolls back to original policy.
No DNS zone quota required — IAM-only changes.

Set env vars:
  SMOKE_OCI_BASELINE_HARDENING=1  (explicit opt-in required)

Run:
  SMOKE_OCI_BASELINE_HARDENING=1 \
  pytest app/tests/executors/test_oci_account_baseline_hardening_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_oci_account_baseline_hardening.log when complete.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

SMOKE_ENABLED = os.environ.get("SMOKE_OCI_BASELINE_HARDENING", "")

pytestmark = pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="Set SMOKE_OCI_BASELINE_HARDENING=1 to run live smoke test",
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
async def test_oci_account_baseline_hardening_all_phases():
    from app.connectors.executors.oci.oci_account_baseline_hardening import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Apply baseline hardening
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Apply OCI Account Baseline Hardening ===")
    result1 = await execute({}, [], connector)
    logger.info("Execute result: %s", result1)
    assert "phases" in result1 or result1.get("status") == "ok", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_oci_account_baseline_hardening.log", "a") as f:
        f.write(f"Phase 1 PASS: hardening applied\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2.get("rolled_back") is True, f"Phase 2 rollback failed: {result2}"
    with open("/tmp/smoke_oci_account_baseline_hardening.log", "a") as f:
        f.write(f"Phase 2 PASS: rolled_back={result2['rolled_back']}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
