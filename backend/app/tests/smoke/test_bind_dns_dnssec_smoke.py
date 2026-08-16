# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""BIND DNS DNSSEC live smoke test.

Requires: BIND connector pointed at a test BIND server.
Set env vars:
  SMOKE_BIND_ZONE        — zone name (e.g. 'test.internal.')
  SMOKE_BIND_ZONE_FILE   — absolute path on server (e.g. '/var/named/test.internal.zone')
"""
import os
import pytest
from app.connectors.executors.bind_dns.bind_dns_dnssec_sign_zone import execute, rollback


@pytest.mark.smoke
@pytest.mark.skipif(not os.getenv("SMOKE_BIND_ZONE"), reason="SMOKE_BIND_ZONE not set")
async def test_bind_dns_dnssec_sign_and_rollback(live_bind_connector):
    params = {
        "zone": os.environ["SMOKE_BIND_ZONE"],
        "zone_file_path": os.environ["SMOKE_BIND_ZONE_FILE"],
    }

    # PHASE 1: Sign
    result = await execute(params, [], live_bind_connector)
    assert result["status"] == "signed"
    assert result["ds_records"]  # at least one DS record extracted

    # PHASE 2: Rollback
    rb = await rollback(params, result, live_bind_connector)
    assert rb["rolled_back"] is True
