# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "user": "ocid1.user.test", "private_key": "key",
        "fingerprint": "fp", "tenancy": "ocid1.tenancy.test", "region": "us-ashburn-1",
    }
    return c


async def test_upgrade_adb(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            m = MagicMock()
            m.db_version = "19c"
            m.lifecycle_state = "AVAILABLE"
            return m
        if call_count[0] == 2:
            return MagicMock()  # update
        m = MagicMock()
        m.db_version = "21c"
        m.lifecycle_state = "AVAILABLE"
        return m

    with patch("app.connectors.executors.oci.oci_adb_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.oci.oci_adb_upgrade import execute
        result = await execute(
            {"adb_id": "ocid1.autonomousdatabase.test", "target_version": "21c"},
            [], connector,
        )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "19c"

    from app.connectors.executors.oci.oci_adb_upgrade import rollback
    rb = await rollback({}, result, connector)
    assert rb["rolled_back"] is False
