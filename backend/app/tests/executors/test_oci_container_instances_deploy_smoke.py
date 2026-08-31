# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test for oci/oci_container_instances_deploy executor.

Requires: OCI container instance (existing — the executor updates the image on it).

Set env vars:
  SMOKE_OCI_COMPARTMENT_ID=<compartment-ocid>
  SMOKE_OCI_INSTANCE_ID=<container-instance-ocid>
  SMOKE_OCI_IMAGE=docker.io/library/nginx:latest  (optional)

Run:
  SMOKE_OCI_COMPARTMENT_ID=... SMOKE_OCI_INSTANCE_ID=... \
  pytest app/tests/executors/test_oci_container_instances_deploy_smoke.py -v -s

ALL_DONE marker written to /tmp/smoke_oci_container_instances_deploy.log when complete.

NOTE: All phases run in a single async test to avoid asyncpg event loop conflicts.
"""

import os
import pytest
import logging

logger = logging.getLogger(__name__)

COMPARTMENT_ID = os.environ.get("SMOKE_OCI_COMPARTMENT_ID", "")
INSTANCE_ID = os.environ.get("SMOKE_OCI_INSTANCE_ID", "")
IMAGE = os.environ.get("SMOKE_OCI_IMAGE", "docker.io/library/nginx:latest")

pytestmark = pytest.mark.skipif(
    not COMPARTMENT_ID or not INSTANCE_ID,
    reason="Set SMOKE_OCI_COMPARTMENT_ID and SMOKE_OCI_INSTANCE_ID to run live smoke test",
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
async def test_oci_container_instances_deploy_all_phases():
    from app.connectors.executors.oci.oci_container_instances_deploy import execute, rollback

    connector = await _load_connector()

    # ------------------------------------------------------------------
    # Phase 1: Deploy new image
    # ------------------------------------------------------------------
    logger.info("=== Phase 1: Deploy new image ===")
    result1 = await execute(
        {"compartment_id": COMPARTMENT_ID, "instance_id": INSTANCE_ID, "image": IMAGE},
        [],
        connector,
    )
    logger.info("Deploy result: %s", result1)
    assert result1.get("status") == "completed", f"Phase 1 failed: {result1}"
    with open("/tmp/smoke_oci_container_instances_deploy.log", "a") as f:
        f.write(f"Phase 1 PASS: deployed {IMAGE}\n")

    # ------------------------------------------------------------------
    # Phase 2: Rollback (restore previous image)
    # ------------------------------------------------------------------
    logger.info("=== Phase 2: Rollback ===")
    result2 = await rollback({}, result1, connector)
    logger.info("Rollback result: %s", result2)
    assert result2["rolled_back"] is True, f"Phase 2 failed: {result2}"
    with open("/tmp/smoke_oci_container_instances_deploy.log", "a") as f:
        f.write(f"Phase 2 PASS: rollback ok\n")
        f.write("ALL_DONE 2 passed\n")
    logger.info("ALL_DONE 2 passed")
