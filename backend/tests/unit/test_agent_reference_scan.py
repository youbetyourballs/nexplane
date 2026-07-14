# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_HIT = {
    "surface": "host_file",
    "location": "/etc/app/config.conf:3",
    "matched_term": "old-db.internal",
    "snippet": "db_host=old-db.internal",
    "consumer_identity": {
        "stable_id": "asset-uuid-123",
        "hostname": "web-01",
        "surface_metadata": {"file_type": "config_file"},
    },
}

_AGENT_OUTPUT_WITH_HIT = json.dumps({
    "hits": [_HIT],
    "scan_summary": {"scanned": 42, "matched": 1},
})

_AGENT_OUTPUT_NO_HIT = json.dumps({
    "hits": [],
    "scan_summary": {"scanned": 10, "matched": 0},
})


def _make_cr(search_terms, asset_id="asset-uuid-123"):
    mock_cr = MagicMock()
    mock_cr.parameters = {
        "search_terms": search_terms,
        "asset_id": asset_id,
    }
    mock_cr.asset_id = asset_id
    return mock_cr


# ---------------------------------------------------------------------------
# Hit-found cases (two file types represented in the single config_file hit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_host_finds_config_file_hit():
    """Executor returns hit when agent reports a config_file match."""
    mock_cr = _make_cr(["old-db.internal"])
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    job_result = {"output": _AGENT_OUTPUT_WITH_HIT, "exit_code": 0}

    with patch(
        "app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value=job_result,
    ):
        from app.connectors.executors.nexplane_agent.scan_host_references import execute
        result = await execute(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "host_file"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == "asset-uuid-123"
    assert hit["consumer_identity"]["hostname"] == "web-01"
    assert hit["consumer_identity"]["surface_metadata"]["file_type"] == "config_file"
    assert result["scan_summary"]["matched"] == 1
    assert result["scan_summary"]["scanned"] == 42


@pytest.mark.asyncio
async def test_scan_host_finds_env_file_hit():
    """Executor returns hit when agent reports an env_file match."""
    env_hit = {**_HIT, "consumer_identity": {**_HIT["consumer_identity"],
        "surface_metadata": {"file_type": "env_file"}}}
    agent_out = json.dumps({"hits": [env_hit], "scan_summary": {"scanned": 5, "matched": 1}})

    mock_cr = _make_cr(["old-db.internal"])
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"output": agent_out, "exit_code": 0},
    ):
        from app.connectors.executors.nexplane_agent.scan_host_references import execute
        result = await execute(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    assert result["hits"][0]["consumer_identity"]["surface_metadata"]["file_type"] == "env_file"


# ---------------------------------------------------------------------------
# No-match cases (two file types)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_host_no_match_config_file():
    """Executor returns empty hits when agent scans config files and finds nothing."""
    mock_cr = _make_cr(["does-not-exist"])
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"output": _AGENT_OUTPUT_NO_HIT, "exit_code": 0},
    ):
        from app.connectors.executors.nexplane_agent.scan_host_references import execute
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["hits"] == []
    assert result["scan_summary"]["matched"] == 0


@pytest.mark.asyncio
async def test_scan_host_no_match_env_file():
    """Executor returns empty hits when agent scans env files and finds nothing."""
    agent_out = json.dumps({"hits": [], "scan_summary": {"scanned": 3, "matched": 0}})
    mock_cr = _make_cr(["ghost-host.internal"])
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"output": agent_out, "exit_code": 0},
    ):
        from app.connectors.executors.nexplane_agent.scan_host_references import execute
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["hits"] == []
    assert result["scan_summary"]["scanned"] == 3


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_host_nonzero_exit_code():
    """Non-zero exit_code returns empty hits with error message."""
    mock_cr = _make_cr(["x"])
    with patch(
        "app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"output": "permission denied", "exit_code": 1},
    ):
        from app.connectors.executors.nexplane_agent.scan_host_references import execute
        result = await execute(mock_cr, MagicMock(), AsyncMock())

    assert result["hits"] == []
    assert "error" in result["scan_summary"]
