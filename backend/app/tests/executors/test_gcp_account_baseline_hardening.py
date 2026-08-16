# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"service_account_key_json": "{}", "project_id": "test-proj"}
    return c


@pytest.mark.asyncio
async def test_execute_runs_all_phases(connector):
    with patch("app.connectors.executors.gcp.gcp_account_baseline_hardening._preflight",
               new=AsyncMock(return_value={"phase": "preflight", "status": "ok", "project_id": "test-proj"})):
        with patch("app.connectors.executors.gcp.gcp_account_baseline_hardening._snapshot",
                   new=AsyncMock(return_value={"phase": "snapshot", "status": "ok", "pre": {}})):
            with patch("app.connectors.executors.gcp.gcp_account_baseline_hardening._enable",
                       new=AsyncMock(return_value={"phase": "enable", "status": "ok",
                                                   "applied": ["compute.requireOsLogin"],
                                                   "skipped": [],
                                                   "rollback_data": {"newly_applied": ["compute.requireOsLogin"]}})):
                with patch("app.connectors.executors.gcp.gcp_account_baseline_hardening._verify",
                           new=AsyncMock(return_value={"phase": "verify", "status": "ok"})):
                    from app.connectors.executors.gcp.gcp_account_baseline_hardening import execute
                    result = await execute({}, [], connector)
    assert result["phase"] == "report"
    assert "applied" in result


@pytest.mark.asyncio
async def test_rollback_restores_policies(connector):
    execution_result = {
        "rollback_data": {
            "newly_applied": ["compute.requireOsLogin", "storage.publicAccessPrevention"],
            "pre_policies": {
                "compute.requireOsLogin": None,
                "storage.publicAccessPrevention": None,
            }
        }
    }
    with patch("app.connectors.executors.gcp.gcp_account_baseline_hardening._run",
               new=AsyncMock(return_value=None)):
        from app.connectors.executors.gcp.gcp_account_baseline_hardening import rollback
        result = await rollback({}, execution_result, connector)
    assert result["rolled_back"] is True
    assert len(result["restored"]) == 2
