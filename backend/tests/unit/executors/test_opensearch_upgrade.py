# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

import app.connectors.executors.nexplane_agent.opensearch_upgrade as os_mod


@pytest.mark.asyncio
async def test_execute_dispatches_opensearch_commands():
    calls = []

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        calls.append(command)
        if command == "app_preflight_opensearch":
            return {"preflight_passed": True, "detected_version": "1.3.19",
                    "distribution": "opensearch"}
        if command == "app_upgrade_opensearch":
            return {"steps_completed": ["upgrade_package"], "target_version": "2.14.0",
                    "upgraded_port": 9201}
        return {}

    with patch.object(os_mod, "dispatch_agent_job", fake_dispatch):
        result = await os_mod.execute(
            parameters={"target_version": "2.14.0", "skip_snapshot": True},
            asset_ids=["asset-1"],
            connector=None,
        )

    assert "app_preflight_opensearch" in calls
    assert "app_upgrade_opensearch" in calls
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_rollback_calls_opensearch_restore():
    calls = []

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        calls.append(command)
        return {"restored": True}

    with patch.object(os_mod, "dispatch_agent_job", fake_dispatch):
        result = await os_mod.rollback(
            parameters={},
            asset_ids=["asset-1"],
            connector=None,
            execution_result={"snapshot_result": {"strategy": "local", "local_path": "/tmp/os_snap.tar.gz"}},
        )

    assert "app_restore_local_opensearch" in calls
    assert result.get("rolled_back") is True
