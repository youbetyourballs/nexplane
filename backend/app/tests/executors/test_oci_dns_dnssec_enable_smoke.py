# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for oci/oci_dns_dnssec_enable executor.

Requires: OCI DNS zone (existing or create via SDK).

Set env vars:
  SMOKE_OCI_ZONE_ID=<zone-ocid>
  SMOKE_OCI_ZONE_NAME=example.com

Run:
  SMOKE_OCI_ZONE_ID=... SMOKE_OCI_ZONE_NAME=... \
  pytest app/tests/executors/test_oci_dns_dnssec_enable_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_oci_dns_dnssec_enable.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ZONE_ID = os.environ.get("SMOKE_OCI_ZONE_ID", "")
ZONE_NAME = os.environ.get("SMOKE_OCI_ZONE_NAME", "")

pytestmark = pytest.mark.skipif(
    not ZONE_ID or not ZONE_NAME,
    reason="Set SMOKE_OCI_ZONE_ID and SMOKE_OCI_ZONE_NAME to run live smoke test",
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
async def test_oci_dns_dnssec_enable_all_phases():
    from app.connectors.executors.oci.oci_dns_dnssec_enable import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Enable DNSSEC
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Enable DNSSEC ===")
    result1 = await execute(
        {"zone_id": ZONE_ID, "zone_name": ZONE_NAME},
        [],
        connector,
    )
    logger.info("Enable result: %s", result1)
    assert result1.get("status") == "enabled", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_oci_dns_dnssec_enable.log", "a") as f:
        f.write(f"Phase 1 PASS: DNSSEC enabled for {ZONE_NAME}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (disable DNSSEC)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (disable DNSSEC) ===")
    result2 = await rollback({"zone_id": ZONE_ID}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_oci_dns_dnssec_enable.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
