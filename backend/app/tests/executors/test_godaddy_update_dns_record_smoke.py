# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for godaddy/update_dns_record executor.

Set env vars:
  SMOKE_GODADDY_DOMAIN=nexplane.ai
  SMOKE_GODADDY_RECORD_NAME=_smoke-test
  SMOKE_GODADDY_RECORD_TYPE=TXT

Run:
  SMOKE_GODADDY_DOMAIN=nexplane.ai SMOKE_GODADDY_RECORD_NAME=_smoke-test \
  SMOKE_GODADDY_RECORD_TYPE=TXT \
  pytest app/tests/executors/test_godaddy_update_dns_record_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_update_dns_record.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

DOMAIN = os.environ.get("SMOKE_GODADDY_DOMAIN", "")
RECORD_NAME = os.environ.get("SMOKE_GODADDY_RECORD_NAME", "")
RECORD_TYPE = os.environ.get("SMOKE_GODADDY_RECORD_TYPE", "TXT")

pytestmark = pytest.mark.skipif(
    not DOMAIN or not RECORD_NAME,
    reason="Set SMOKE_GODADDY_DOMAIN and SMOKE_GODADDY_RECORD_NAME to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "godaddy").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No GoDaddy connector found in platform — add one before running smoke")
        from app.services.connector_service import _attach_credentials
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_godaddy_update_dns_record_all_phases():
    from app.connectors.executors.godaddy.update_dns_record import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Execute (set TXT record)
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Execute (set TXT record) ===")
    result1 = await execute(
        {
            "domain": DOMAIN,
            "record_type": RECORD_TYPE,
            "name": RECORD_NAME,
            "value": "nexplane-smoke-test-verify",
            "ttl": 600,
        },
        [],
        connector,
    )
    logger.info("Execute result: %s", result1)
    assert result1["domain"] == DOMAIN, f"Phase 1 failed: {result1}"
    assert result1["value"] == "nexplane-smoke-test-verify"
    with open("/tmp/smoke_update_dns_record.log", "a") as f:
        f.write(f"Phase 1 PASS: set {RECORD_TYPE} {RECORD_NAME}.{DOMAIN}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (restore original)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (restore original) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_update_dns_record.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
