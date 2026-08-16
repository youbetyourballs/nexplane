# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""GCP Cloud DNS DNSSEC live smoke test.

Requires: GCP connector with a test Cloud DNS zone.
Set env vars:
  SMOKE_GCP_ZONE_NAME  — Cloud DNS zone name (not DNS name)
"""
import os
import pytest
from app.connectors.executors.gcp.gcp_cloud_dns_dnssec_enable import execute, rollback


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_GCP_ZONE_NAME"), reason="SMOKE_GCP_ZONE_NAME not set")
async def test_gcp_dns_dnssec_enable_and_rollback(live_gcp_connector):
    zone_name = os.environ["SMOKE_GCP_ZONE_NAME"]
    params = {"zone_name": zone_name}

    # PHASE 1: Enable
    result = await execute(params, [], live_gcp_connector)
    assert result["status"] == "signed"

    # PHASE 2: Idempotency
    result2 = await execute(params, [], live_gcp_connector)
    assert result2["already_enabled"] is True

    # PHASE 3: Rollback
    rb = await rollback(params, result, live_gcp_connector)
    assert rb["rolled_back"] is True
