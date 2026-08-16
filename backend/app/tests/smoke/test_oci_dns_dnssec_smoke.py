# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""OCI DNS DNSSEC live smoke test.

Requires: OCI connector with a test DNS zone.
Set env var:
  SMOKE_OCI_DNS_ZONE_ID  — OCI DNS zone OCID or name
"""
import os
import pytest
from app.connectors.executors.oci.oci_dns_dnssec_enable import execute, rollback


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_OCI_DNS_ZONE_ID"), reason="SMOKE_OCI_DNS_ZONE_ID not set")
async def test_oci_dns_dnssec_enable_and_rollback(live_oci_connector):
    zone_id = os.environ["SMOKE_OCI_DNS_ZONE_ID"]
    params = {"zone_id": zone_id}

    # PHASE 1: Enable
    result = await execute(params, [], live_oci_connector)
    assert result["status"] == "enabled"

    # PHASE 2: Idempotency
    result2 = await execute(params, [], live_oci_connector)
    assert result2["already_enabled"] is True

    # PHASE 3: Rollback
    rb = await rollback(params, result, live_oci_connector)
    assert rb["rolled_back"] is True
