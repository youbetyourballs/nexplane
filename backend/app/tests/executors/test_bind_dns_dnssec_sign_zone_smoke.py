# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for bind_dns/bind_dns_dnssec_sign_zone executor.

Requires: EC2 t3.micro with BIND9 installed, registered as a nexplane_agent asset.
AMI cache pattern: /nexplane/smoke-amis/bind9/{hash}

Set env vars:
  SMOKE_BIND_ASSET_ID=<asset-uuid>
  SMOKE_BIND_ZONE=example.com
  SMOKE_BIND_ZONE_FILE_PATH=/etc/named/zones/db.example.com  (optional)

Run:
  SMOKE_BIND_ASSET_ID=... SMOKE_BIND_ZONE=example.com \
  pytest app/tests/executors/test_bind_dns_dnssec_sign_zone_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_bind_dns_dnssec_sign_zone.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ASSET_ID = os.environ.get("SMOKE_BIND_ASSET_ID", "")
ZONE = os.environ.get("SMOKE_BIND_ZONE", "")
ZONE_FILE_PATH = os.environ.get("SMOKE_BIND_ZONE_FILE_PATH", "")

pytestmark = pytest.mark.skipif(
    not ASSET_ID or not ZONE,
    reason="Set SMOKE_BIND_ASSET_ID and SMOKE_BIND_ZONE to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.services.connector_service import _attach_credentials
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "nexplane_agent").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No nexplane_agent connector found in platform")
        await _attach_credentials(c, db)
        return c


@pytest.mark.asyncio
async def test_bind_dns_dnssec_sign_zone_all_phases():
    from app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone import execute, rollback

    connector = await _load_connector()
    zone_file = ZONE_FILE_PATH or f"/etc/named/zones/db.{ZONE}"

    # ------------------------------------------------------------------
    # Phase 1: Sign zone
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Sign zone ===")
    result1 = await execute(
        {"zone": ZONE, "zone_file_path": zone_file},
        [ASSET_ID],
        connector,
    )
    logger.info("Sign result: %s", result1)
    assert result1.get("status") == "signed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_bind_dns_dnssec_sign_zone.log", "a") as f:
        f.write(f"Phase 1 PASS: zone signed, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (restore unsigned zone)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback (restore unsigned zone) ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_bind_dns_dnssec_sign_zone.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
