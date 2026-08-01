# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import AsyncMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

import app.connectors.executors.nexplane_agent.elasticsearch_upgrade as es_mod


@pytest.mark.asyncio
async def test_execute_dispatches_preflight_then_upgrade():
    dispatch_calls = []

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append(command)
        if command == "app_preflight_elasticsearch":
            return {"preflight_passed": True, "detected_version": "7.17.0"}
        if command == "app_upgrade_elasticsearch":
            return {"steps_completed": ["upgrade_package"], "target_version": "8.14.0", "upgraded_port": 9201}
        return {}

    with patch.object(es_mod, "dispatch_agent_job", fake_dispatch):
        executor = es_mod.ElasticsearchUpgradeExecutor()
        result = await executor.execute(
            parameters={"target_version": "8.14.0", "skip_snapshot": True},
            asset_ids=["asset-1"],
            connector=None,
        )

    assert "app_preflight_elasticsearch" in dispatch_calls
    assert "app_upgrade_elasticsearch" in dispatch_calls
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_execute_blocks_on_preflight_critical():
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        if command == "app_preflight_elasticsearch":
            return {
                "preflight_passed": False,
                "status": "preflight_blocked",
                "reason": "cluster health is RED",
                "findings": [{"severity": "CRITICAL", "check": "cluster_health", "detail": "RED"}],
            }
        return {}

    with patch.object(es_mod, "dispatch_agent_job", fake_dispatch):
        executor = es_mod.ElasticsearchUpgradeExecutor()
        result = await executor.execute(
            parameters={"target_version": "8.14.0"},
            asset_ids=["asset-1"],
            connector=None,
        )

    assert result["status"] == "preflight_blocked"
    assert "app_upgrade_elasticsearch" not in str(result)


@pytest.mark.asyncio
async def test_rollback_dispatches_restore_command():
    dispatch_calls = []

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append(command)
        return {"restored": True}

    with patch.object(es_mod, "dispatch_agent_job", fake_dispatch):
        executor = es_mod.ElasticsearchUpgradeExecutor()
        result = await executor.rollback(
            asset_id="asset-1",
            execution_result={
                "snapshot_result": {"strategy": "local", "local_path": "/tmp/es_snap.tar.gz"},
            },
            connector=None,
        )

    assert "app_restore_local_elasticsearch" in dispatch_calls
    assert result.get("rolled_back") is True
