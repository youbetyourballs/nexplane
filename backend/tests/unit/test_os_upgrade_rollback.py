# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


SNAP_META = {
    "snapshot_id": "snap-abc123",
    "root_volume_id": "vol-old",
    "root_device_name": "/dev/xvda",
    "availability_zone": "us-east-1a",
    "region": "us-east-1",
    "instance_id": "i-test123",
}


@pytest.mark.asyncio
async def test_rollback_no_snapshot():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback
    result = await rollback({}, {}, None)
    assert result["rolled_back"] is False
    assert result["reason"] == "no_snapshot_available"


@pytest.mark.asyncio
async def test_rollback_no_snapshot_meta_falls_back_to_manual():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback
    result = await rollback({}, {"snapshot_id": "snap-abc"}, None)
    assert result["rolled_back"] is False
    assert "manual_steps" in result


@pytest.mark.asyncio
async def test_rollback_calls_restore_snapshot():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback

    mock_restore = AsyncMock(return_value={
        "restored": True,
        "new_volume_id": "vol-new",
        "old_volume_id": "vol-old",
        "agent_recovered": True,
    })
    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._restore_snapshot",
        mock_restore,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            MagicMock(),
        )
    assert result["rolled_back"] is True
    assert result["new_volume_id"] == "vol-new"
    assert result["agent_recovered"] is True
    mock_restore.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_restore_exception_returns_false():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback

    mock_restore = AsyncMock(side_effect=RuntimeError("EC2 API error"))
    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._restore_snapshot",
        mock_restore,
    ):
        result = await rollback(
            {},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            MagicMock(),
        )
    assert result["rolled_back"] is False
    assert "EC2 API error" in result["reason"]


@pytest.mark.asyncio
async def test_snapshot_only_parameter():
    from app.connectors.executors.nexplane_agent.os_upgrade import execute

    test_asset_id = "00000000-0000-0000-0000-000000000001"

    mock_snap = AsyncMock(return_value=SNAP_META)
    mock_asset = MagicMock()
    mock_asset.asset_metadata = {"instance_id": "i-test123"}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=mock_asset)

    mock_dispatch = AsyncMock(return_value={"status": "ok", "current_os": "Ubuntu 20.04"})

    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._take_snapshot", mock_snap
    ), patch(
        "app.database.AsyncSessionLocal",
        return_value=mock_db,
    ), patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await execute(
            {"snapshot_only": True},
            [test_asset_id],
            MagicMock(credentials={"access_key_id": "k", "secret_access_key": "s"}),
        )
    assert result["status"] == "snapshot_only"
    assert result["snapshot_id"] == "snap-abc123"
    assert result["snapshot_meta"] == SNAP_META
