# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

SAMPLE_BASELINE = {
    "captured_at": "2026-07-13T12:00:00Z",
    "observation_duration_seconds": 1200,
    "endpoints": [{"url": "http://localhost:80", "probe_type": "http_get",
                   "status_code": 200, "response_ms_p50": 12, "response_ms_p95": 35,
                   "content_signature": "abc123", "confidence": "both"}],
    "services": [{"name": "nginx", "state": "active", "active_connections": 2, "port": 80}],
    "dependencies": [{"target": "localhost:5432", "type": "db", "latency_ms_p50": 3,
                      "query_sample_result": "1", "row_count_sample": 4923, "confidence": "both"}],
    "library_versions": [{"name": "libpq", "version": "14.0"}],
}


@pytest.mark.asyncio
async def test_execute_returns_baseline():
    with patch(
        "app.connectors.executors.nexplane_agent.capture_behavioral_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"baseline": SAMPLE_BASELINE, "unverified_dependencies": []},
    ):
        from app.connectors.executors.nexplane_agent import capture_behavioral_baseline as m

        result = await m.execute(
            parameters={"observation_window_seconds": 1200},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["action"] == "capture_behavioral_baseline"
    assert result["baseline"]["endpoints"][0]["status_code"] == 200
    assert result["unverified_dependencies"] == []


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import capture_behavioral_baseline as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
