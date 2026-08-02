# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_mock_mode_returns_structure():
    from app.connectors.executors.gcp.gcp_account_baseline_monitoring import execute
    connector = MagicMock()
    connector.credentials = {}
    result = await execute({}, [], connector)
    assert result["mock"] is True
    assert result["promote_to"] == "gcp_account_baseline_hardening"
    assert "phases" in result
    assert "summary" in result
    assert "rollback_data" in result


@pytest.mark.asyncio
async def test_rollback_capability():
    from app.connectors.executors.gcp.gcp_account_baseline_monitoring import ROLLBACK_CAPABILITY
    assert ROLLBACK_CAPABILITY == "full"


@pytest.mark.asyncio
async def test_rollback_mock_no_newly_enabled():
    from app.connectors.executors.gcp.gcp_account_baseline_monitoring import rollback
    connector = MagicMock()
    connector.credentials = {}
    result = await rollback({}, {"rollback_data": {"newly_enabled": [], "pre_existing_states": {}}}, connector)
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_mock_summary_shape():
    from app.connectors.executors.gcp.gcp_account_baseline_monitoring import execute
    connector = MagicMock()
    connector.credentials = {}
    result = await execute({}, [], connector)
    summary = result["summary"]
    assert "already_enabled" in summary
    assert "newly_enabled" in summary
    assert "failed" in summary
    assert "skipped_with_warning" in summary
