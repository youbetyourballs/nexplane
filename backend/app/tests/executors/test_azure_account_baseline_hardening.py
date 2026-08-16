# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "tenant_id": "t1", "client_id": "c1",
        "client_secret": "s1", "subscription_id": "sub1",
    }
    return c


async def test_execute_runs_phases(connector):
    with patch("app.connectors.executors.azure.azure_account_baseline_hardening._preflight",
               new=AsyncMock(return_value={"phase": "preflight", "status": "ok", "subscription_id": "sub1"})):
        with patch("app.connectors.executors.azure.azure_account_baseline_hardening._snapshot",
                   new=AsyncMock(return_value={"phase": "snapshot", "status": "ok", "pre": {}})):
            with patch("app.connectors.executors.azure.azure_account_baseline_hardening._enable",
                       new=AsyncMock(return_value={"phase": "enable", "status": "ok",
                                                   "applied": ["nexplane-cis-baseline"],
                                                   "skipped": [],
                                                   "rollback_data": {"assignment_names": ["nexplane-cis-baseline"]}})):
                with patch("app.connectors.executors.azure.azure_account_baseline_hardening._verify",
                           new=AsyncMock(return_value={"phase": "verify", "status": "ok", "failures": []})):
                    from app.connectors.executors.azure.azure_account_baseline_hardening import execute
                    result = await execute({}, [], connector)
    assert result["phase"] == "report"
    assert "applied" in result


async def test_rollback_deletes_assignments(connector):
    execution_result = {
        "subscription_id": "sub1",
        "rollback_data": {"assignment_names": ["nexplane-cis-baseline"]},
    }
    with patch("app.connectors.executors.azure.azure_account_baseline_hardening._run",
               new=AsyncMock(return_value=None)):
        from app.connectors.executors.azure.azure_account_baseline_hardening import rollback
        result = await rollback({}, execution_result, connector)
    assert result["rolled_back"] is True
