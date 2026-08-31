# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for nexplane_agent/db_upgrade_postgres_in_place executor.

Requires: EC2 with PostgreSQL 14 installed (provision via userdata), registered as
an asset in the platform with the nexplane-agent installed.

Set env vars:
  SMOKE_PG_ASSET_ID=<asset-uuid>
  SMOKE_PG_TARGET_VERSION=15

Run:
  SMOKE_PG_ASSET_ID=... SMOKE_PG_TARGET_VERSION=15 \
  pytest app/tests/executors/test_db_upgrade_postgres_in_place_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_db_upgrade_postgres_in_place.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ASSET_ID = os.environ.get("SMOKE_PG_ASSET_ID", "")
TARGET_VERSION = os.environ.get("SMOKE_PG_TARGET_VERSION", "")

pytestmark = pytest.mark.skipif(
    not ASSET_ID or not TARGET_VERSION,
    reason="Set SMOKE_PG_ASSET_ID and SMOKE_PG_TARGET_VERSION to run live smoke test",
)


async def _load_connector():
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "aws").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No AWS connector found in platform — needed for EBS snapshot")
        return c


@pytest.mark.asyncio
async def test_db_upgrade_postgres_in_place_all_phases():
    from app.connectors.executors.nexplane_agent.db_upgrade_postgres_in_place import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Upgrade (preflight + snapshot + pg_upgrade + verify)
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Upgrade PostgreSQL to %s ===", TARGET_VERSION)
    result1 = await execute(
        {"target_version": TARGET_VERSION},
        [ASSET_ID],
        connector,
    )
    logger.info("Upgrade result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    assert result1.get("new_version"), "new_version missing from result"
    with open("/tmp/smoke_db_upgrade_postgres_in_place.log", "a") as f:
        f.write(
            f"Phase 1 PASS: upgraded to {result1['new_version']}, "
            f"snapshot={result1.get('snapshot_id')}\n"
        )

    # ------------------------------------------------------------------
    # Phase 2: Rollback (EBS restore or rollback.sh)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_db_upgrade_postgres_in_place.log", "a") as f:
        f.write(
            f"Phase 2 PASS: rollback ok, strategy={result2.get('strategy')}\n"
        )
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
