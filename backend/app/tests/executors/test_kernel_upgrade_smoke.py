# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for kernel_upgrade executor.

Requires: a real EC2 instance registered as an asset in the platform with:
- Amazon Linux 2023 (kernel 6.1.x) or Ubuntu 22.04 (kernel 5.15.x)
- nexplane agent installed and registered
- At least 10 GB root volume

Set env var: SMOKE_KERNEL_ASSET_ID=<asset-uuid>
             SMOKE_TARGET_KERNEL=<kernel-version>   (e.g. "kernel-6.1.82-99.174.amzn2023.x86_64")
             SMOKE_ROLLBACK_KERNEL=<old-kernel>     (currently running kernel before upgrade)

Run:
  SMOKE_KERNEL_ASSET_ID=... SMOKE_TARGET_KERNEL=... SMOKE_ROLLBACK_KERNEL=... \
  pytest app/tests/executors/test_kernel_upgrade_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_kernel_upgrade.log when complete.
"""

import asyncio
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

_PHASE2_RESULT = {}  # filled by test_phase2_execute_upgrade


@pytest.fixture(scope="module")
def connector():
    """Load the real AWS connector from the platform database."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async def _load():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Connector).where(Connector.connector_type == "aws").limit(1)
            )
            c = result.scalar_one_or_none()
            if not c:
                raise RuntimeError("No AWS connector found in platform — add one before running smoke")
            return c

    return asyncio.run(_load())


@pytest.mark.asyncio
async def test_phase1_preflight(connector):
    """Phase 1: preflight should pass for a valid target kernel."""
    from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
    result = await execute(
        {"target_kernel": TARGET_KERNEL, "dry_run": True},
        [ASSET_ID],
        connector,
    )
    logger.info("Preflight result: %s", result)
    assert result["status"] in ("dry_run", "completed"), f"Preflight failed: {result}"
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(f"Phase 1 PASS: preflight ok, result={result}\n")


@pytest.mark.asyncio
async def test_phase2_execute_upgrade(connector):
    """Phase 2: full upgrade — installs kernel, reboots, verifies."""
    from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
    result = await execute(
        {"target_kernel": TARGET_KERNEL},
        [ASSET_ID],
        connector,
    )
    logger.info("Upgrade result: %s", result)
    assert result["status"] == "completed", f"Upgrade failed: {result}"
    assert result["new_kernel"], "new_kernel missing from result"
    assert result["snapshot_id"], "snapshot_id missing — EBS snapshot was not taken"
    assert result.get("services_verified") is True, f"Service health check failed: {result}"
    _PHASE2_RESULT.update(result)
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(f"Phase 2 PASS: upgrade completed, new_kernel={result['new_kernel']}, snap={result['snapshot_id']}, services_verified={result.get('services_verified')}\n")


@pytest.mark.asyncio
async def test_phase3_rollback(connector):
    """Phase 3: rollback to previous kernel via GRUB fallback."""
    from app.connectors.executors.nexplane_agent.kernel_upgrade import rollback
    # Use EBS restore path (primary production path) when Phase 2 captured a snapshot,
    # otherwise fall back to GRUB-only path.
    execution_result = {
        "asset_id": ASSET_ID,
        "previous_kernel": ROLLBACK_KERNEL,
    }
    if _PHASE2_RESULT.get("snapshot_id"):
        execution_result.update({
            "snapshot_id": _PHASE2_RESULT["snapshot_id"],
            "instance_id": _PHASE2_RESULT.get("instance_id"),
            "root_volume_id": _PHASE2_RESULT.get("root_volume_id"),
            "root_device_name": _PHASE2_RESULT.get("root_device_name"),
            "availability_zone": _PHASE2_RESULT.get("availability_zone"),
            "region": _PHASE2_RESULT.get("region"),
        })
    result = await rollback({}, execution_result, connector)
    logger.info("Rollback result: %s", result)
    assert result["rolled_back"] is True, f"Rollback failed: {result}"
    with open("/tmp/smoke_kernel_upgrade.log", "a") as f:
        f.write(f"Phase 3 PASS: rollback completed, strategy={result.get('strategy')}\n")
        f.write("ALL_DONE 3 passed\n")
