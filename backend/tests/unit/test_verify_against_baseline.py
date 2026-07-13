# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

PASS_REPORT = {
    "layers": {
        "infrastructure": {"passed": True, "checks": []},
        "service": {"passed": True, "checks": []},
        "application": {"passed": True, "checks": []},
        "data": {"passed": True, "checks": []},
    },
    "summary": "All four layers passed.",
}

FAIL_REPORT = {
    "layers": {
        "infrastructure": {"passed": True, "checks": []},
        "service": {"passed": True, "checks": []},
        "application": {"passed": False, "checks": [
            {"name": "http_status", "expected": 200, "actual": 502, "passed": False}
        ]},
        "data": {"passed": True, "checks": []},
    },
    "summary": "Application layer failed: http_status mismatch.",
}


@pytest.mark.asyncio
async def test_execute_pass():
    with patch(
        "app.connectors.executors.nexplane_agent.verify_against_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"report": PASS_REPORT},
    ):
        from app.connectors.executors.nexplane_agent import verify_against_baseline as m

        result = await m.execute(
            parameters={},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["action"] == "verify_against_baseline"
    assert result["failed"] is False
    assert result["layers"]["application"]["passed"] is True


@pytest.mark.asyncio
async def test_execute_application_layer_fail_sets_failed_true():
    with patch(
        "app.connectors.executors.nexplane_agent.verify_against_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"report": FAIL_REPORT},
    ):
        from app.connectors.executors.nexplane_agent import verify_against_baseline as m

        result = await m.execute(
            parameters={},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["failed"] is True


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import verify_against_baseline as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
