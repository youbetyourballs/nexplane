# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for kernel_upgrade executor.

Requires: a real EC2 instance registered as an asset in the platform with:
- Amazon Linux 2023 (kernel 6.1.x) or Ubuntu 22.04 (kernel 5.15.x)
- nexplane agent installed and registered
- At least 10 GB root volume

Set env var: SMOKE_KERNEL_ASSET_ID=<asset-uuid>
             SMOKE_TARGET_KERNEL=<kernel-version>   (e.g. "6.1.180-225.360.amzn2023.x86_64")
             SMOKE_ROLLBACK_KERNEL=<old-kernel>     (currently running kernel before upgrade)

Run:
  SMOKE_KERNEL_ASSET_ID=... SMOKE_TARGET_KERNEL=... SMOKE_ROLLBACK_KERNEL=... \
  pytest app/tests/executors/test_kernel_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_kernel_upgrade.log when complete.

NOTE: All three phases run in a single async test to avoid asyncpg "Future attached
to a different loop" errors that occur when pytest-asyncio creates a new event loop
per test function while the global engine pool retains connections from prior loops.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

ASSET_ID = os.environ.get("SMOKE_KERNEL_ASSET_ID", "")
TARGET_KERNEL = os.environ.get("SMOKE_TARGET_KERNEL", "")
ROLLBACK_KERNEL = os.environ.get("SMOKE_ROLLBACK_KERNEL", "")

pytestmark = pytest.mark.skipif(
    not ASSET_ID or not TARGET_KERNEL,
    reason="Set SMOKE_KERNEL_ASSET_ID and SMOKE_TARGET_KERNEL to run live smoke test",
)


async def _load_connector():
    """Load first AWS connector from DB within the calling test's event loop."""
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Connector).where(Connector.connector_type == "aws").limit(1)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise RuntimeError("No AWS connector found in platform — add one before running smoke")
        return c


@pytest.mark.asyncio
async def test_kernel_upgrade_all_phases():
    """Run all three phases (preflight, upgrade, rollback) in one event loop.

    A single async test avoids the asyncpg 'Future attached to a different loop'
    error that arises when pytest-asyncio creates a new event loop per test while
    the global engine connection pool retains connections from the previous loop.
    """
    from app.connectors.executors.nexplane_agent.kernel_upgrade import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: preflight dry_run
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Preflight (dry_run) ===")
    result1 = await execute(
        {"target_kernel": TARGET_KERNEL, "dry_run": True},
        [ASSET_ID],
        connector,
    )
    logger.info("Preflight result: %s", result1)
    assert result1["status"] in ("dry_run", "completed"), f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: preflight ok, status={result1['status']}\n")

    # ------------------------------------------------------------------
    # Phase 2: full upgrade (installs kernel, reboots, verifies)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Full Upgrade ===")
    result2 = await execute(
        {"target_kernel": TARGET_KERNEL},
        [ASSET_ID],
        connector,
    )
    logger.info("Upgrade result: %s", result2)
    assert result2["status"] == "completed", f"Phase 2 failed: {result2}"
    assert result2.get("new_kernel"), "new_kernel missing from result"
    assert result2.get("snapshot_id"), "snapshot_id missing — EBS snapshot was not taken"
    assert result2.get("services_verified") is True, f"Service health check failed: {result2}"
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(
            f"Phase 2 PASS: upgrade completed, new_kernel={result2['new_kernel']}, "
            f"snap={result2['snapshot_id']}, services_verified={result2.get('services_verified')}\n"
        )

    # ------------------------------------------------------------------
    # Phase 3: rollback
    # ------------------------------------------------------------------
    logger.info("=== Phase 3: Rollback ===")
    execution_result = {
        "asset_id": ASSET_ID,
        "previous_kernel": ROLLBACK_KERNEL,
    }
    if result2.get("snapshot_id"):
        execution_result.update({
            "snapshot_id": result2["snapshot_id"],
            "instance_id": result2.get("instance_id"),
            "root_volume_id": result2.get("root_volume_id"),
            "root_device_name": result2.get("root_device_name"),
            "availability_zone": result2.get("availability_zone"),
            "region": result2.get("region"),
        })
    result3 = await rollback({}, execution_result, connector)
    logger.info("Rollback result: %s", result3)
    assert result3["rolled_back"] is True, f"Phase 3 failed: {result3}"
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(f"Phase 3 PASS: rollback completed, strategy={result3.get('strategy')}\n")
        f.write("ALL_DONE 3 passed\n")
    logger.info("ALL_DONE 3 passed")
