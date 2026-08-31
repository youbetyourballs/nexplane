# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for bind_dns/dns_zone_migrate executor + rollback_dns_zone_migrate rollback.

Requires: Same BIND EC2 used for bind_dns_dnssec_sign_zone smoke (Task 3).

Set env vars:
  SMOKE_BIND_ASSET_ID=<asset-uuid>
  SMOKE_BIND_ZONE=example.com
  SMOKE_BIND_SERVER=<bind-server-ip>  (optional — uses TSIG from connector creds)

Run:
  SMOKE_BIND_ASSET_ID=... SMOKE_BIND_ZONE=example.com SMOKE_BIND_SERVER=... \
  pytest app/tests/executors/test_bind_dns_zone_migrate_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_dns_zone_migrate.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ASSET_ID = os.environ.get("SMOKE_BIND_ASSET_ID", "")
ZONE = os.environ.get("SMOKE_BIND_ZONE", "")
SERVER = os.environ.get("SMOKE_BIND_SERVER", "127.0.0.1")

pytestmark = pytest.mark.skipif(
    not ASSET_ID or not ZONE,
    reason="Set SMOKE_BIND_ASSET_ID and SMOKE_BIND_ZONE to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "nexplane_agent").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No nexplane_agent connector found in platform")
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
            "zone": ZONE,
            "new_ns": ["ns1.smoke-target.example.", "ns2.smoke-target.example."],
            "new_ttl": 60,
            "server": SERVER,
        },
        [ASSET_ID],
        connector,
    )
    logger.info("Migrate result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_dns_zone_migrate.log", "a") as f:
        f.write(f"Phase 1 PASS: zone migrated, status={result1['status']}\n")

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
