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


@pytest.mark.asyncio
async def test_execute_runs_phases(connector):
    with patch("app.connectors.executors.oci.oci_account_baseline_hardening._preflight",
               new=AsyncMock(return_value={"phase": "preflight", "status": "ok",
                                           "tenancy_id": "ocid1.tenancy.test"})):
        with patch("app.connectors.executors.oci.oci_account_baseline_hardening._snapshot",
                   new=AsyncMock(return_value={"phase": "snapshot", "status": "ok", "pre": {}})):
            with patch("app.connectors.executors.oci.oci_account_baseline_hardening._enable",
                       new=AsyncMock(return_value={"phase": "enable", "status": "ok",
                                                   "applied": ["password_policy"],
                                                   "skipped": [],
                                                   "rollback_data": {"prior_password_policy": None}})):
                with patch("app.connectors.executors.oci.oci_account_baseline_hardening._verify",
                           new=AsyncMock(return_value={"phase": "verify", "status": "ok", "failures": []})):
                    from app.connectors.executors.oci.oci_account_baseline_hardening import execute
                    result = await execute({}, [], connector)
    assert result["phase"] == "report"
    assert "applied" in result
