# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for bind_dns/dns_zone_migrate executor + rollback_dns_zone_migrate rollback.

Requires: BIND9 EC2 with TSIG key configured, and a bind_dns connector in the platform
pointing to the BIND9 server.

Set env vars:
  SMOKE_BIND_ZONE=smoke.nexplane.internal

Run:
  SMOKE_BIND_ZONE=smoke.nexplane.internal \
  pytest app/tests/executors/test_bind_dns_zone_migrate_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_dns_zone_migrate.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ZONE = os.environ.get("SMOKE_BIND_ZONE", "")

pytestmark = pytest.mark.skipif(
    not ZONE,
    reason="Set SMOKE_BIND_ZONE to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.services.connector_service import _attach_credentials
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "bind_dns").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No bind_dns connector found in platform")
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_dns_zone_migrate_all_phases():
    from app.connectors.executors.bind_dns.dns_zone_migrate import execute
    from app.connectors.executors.bind_dns.rollback_dns_zone_migrate import rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Lower TTL + switch NS
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Lower TTL + switch NS ===")
    result1 = await execute(
        {
            "source_zone": ZONE,
            "target_nameservers": ["ns1.smoke-target.example.", "ns2.smoke-target.example."],
            "ttl_lower_value": 60,
            "propagation_wait_seconds": 0,
            "verify_resolvers": [],
        },
        [],
        connector,
    )
    logger.info("Migrate result: %s", result1)
    assert result1.get("rollback_data"), f"Phase 1 failed — no rollback_data: {result1}"
    with open("/tmp/smoke_dns_zone_migrate.log", "a") as f:
        f.write(f"Phase 1 PASS: zone migrated\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (restore original NS + TTL)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (restore original NS + TTL) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_dns_zone_migrate.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok, restored_ns={result2.get('restored_ns')}\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
