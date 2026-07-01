# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Verify configure_seccomp rollback passes correct parameters to the agent dispatcher."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_rollback_passes_snapshot_and_service_name():
    """Rollback must pass 'snapshot' and 'service_name', not 'snapshot_id'."""
    from app.connectors.executors.nexplane_agent import configure_seccomp

    execution_result = {
        "_asset_ids": ["asset-uuid-1"],
        "service_name": "nginx",
        "snapshot": "[Service]\nSeccompFilter=/old/path.json\n",
    }
    parameters = {"service_name": "nginx", "profile": "{}"}

    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured["command"] = command
        captured["parameters"] = parameters
        captured["asset_ids"] = asset_ids
        return {"rolled_back": True}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        result = await configure_seccomp.rollback(parameters, execution_result, connector=None)

    assert captured["command"] == "configure_seccomp"
    assert captured["parameters"]["snapshot"] == "[Service]\nSeccompFilter=/old/path.json\n"
    assert captured["parameters"]["service_name"] == "nginx"
    assert "snapshot_id" not in captured["parameters"]
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_rollback_with_no_snapshot_passes_empty_string():
    """When no prior config existed, snapshot should be empty string (agent removes the drop-in)."""
    from app.connectors.executors.nexplane_agent import configure_seccomp

    execution_result = {
        "_asset_ids": ["asset-uuid-1"],
        "service_name": "nginx",
        "snapshot": "",
    }
    parameters = {"service_name": "nginx", "profile": "{}"}

    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured["parameters"] = parameters
        return {"rolled_back": True}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        await configure_seccomp.rollback(parameters, execution_result, connector=None)

    assert captured["parameters"]["snapshot"] == ""
    assert captured["parameters"]["service_name"] == "nginx"
