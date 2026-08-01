# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import AsyncMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from app.connectors.executors.nexplane_agent.app_upgrade_base import (
    AppUpgradeExecutor, PreflightBlocked, SNAPSHOT_STRATEGY_EBS,
    SNAPSHOT_STRATEGY_S3, SNAPSHOT_STRATEGY_LOCAL,
)

class ConcreteUpgradeExecutor(AppUpgradeExecutor):
    async def preflight(self, asset_id, p, connector):
        return {"preflight_passed": True}

    async def upgrade(self, asset_id, p, connector):
        return {"upgraded": True}

    async def verify(self, asset_id, p, connector, upgrade_result):
        return {"verify_status": "passed"}

    async def rollback(self, asset_id, execution_result, connector):
        return {"rolled_back": True}


@pytest.mark.asyncio
async def test_execute_dry_run_skips_snapshot_and_upgrade():
    executor = ConcreteUpgradeExecutor()
    with patch.object(executor, "take_snapshot", new_callable=AsyncMock) as mock_snap:
        result = await executor.execute(
            parameters={"dry_run": True, "target_version": "8.0"},
            asset_ids=["asset-1"],
            connector=None,
        )
    mock_snap.assert_not_called()
    assert result.get("preflight_passed") is True


@pytest.mark.asyncio
async def test_execute_preflight_blocked_returns_early():
    class BlockingExecutor(AppUpgradeExecutor):
        async def preflight(self, asset_id, p, connector):
            raise PreflightBlocked("disk too small")
        async def upgrade(self, asset_id, p, connector): ...
        async def verify(self, asset_id, p, connector, upgrade_result): ...
        async def rollback(self, asset_id, execution_result, connector): ...

    executor = BlockingExecutor()
    with patch.object(executor, "take_snapshot", new_callable=AsyncMock) as mock_snap:
        result = await executor.execute(
            parameters={"target_version": "8.0"},
            asset_ids=["asset-1"],
            connector=None,
        )
    mock_snap.assert_not_called()
    assert result["status"] == "preflight_blocked"
    assert "disk too small" in result["reason"]


@pytest.mark.asyncio
async def test_execute_happy_path_returns_completed():
    executor = ConcreteUpgradeExecutor()
    with patch.object(executor, "take_snapshot", new_callable=AsyncMock,
                      return_value={"strategy": SNAPSHOT_STRATEGY_LOCAL, "local_path": "/tmp/snap"}):
        result = await executor.execute(
            parameters={"target_version": "8.0"},
            asset_ids=["asset-1"],
            connector=None,
        )
    assert result["status"] == "completed"
    assert result["snapshot_result"]["strategy"] == SNAPSHOT_STRATEGY_LOCAL
    assert result["upgrade_result"]["upgraded"] is True
    assert result["verify_result"]["verify_status"] == "passed"
