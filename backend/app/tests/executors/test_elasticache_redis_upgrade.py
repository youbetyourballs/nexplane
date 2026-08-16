# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "access_key_id": "k",
        "secret_access_key": "s",
        "region": "us-east-1",
    }
    return c


async def test_already_at_version(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            # _get_group → group dict with MemberClusters
            return {"MemberClusters": ["c-001"]}
        # _get_cluster_version → version string matching target
        return "7.1.0"

    with patch("app.connectors.executors.aws.elasticache_redis_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.aws.elasticache_redis_upgrade import execute
        result = await execute(
            {"replication_group_id": "my-cluster", "target_version": "7.1.0"},
            [],
            connector,
        )
    assert result["status"] == "already_at_version"
    assert result["current_version"] == "7.1.0"


async def test_upgrade_triggers_modify(connector):
    call_log = []

    async def fake_run(fn):
        call_log.append(True)
        n = len(call_log)
        if n == 1:
            # _get_group → returns group dict; executor reads MemberClusters from it
            return {
                "MemberClusters": ["c-001"],
            }
        if n == 2:
            # _get_cluster_version → returns the engine version string
            return "6.2.7"
        if n == 3:
            # _get_snapshot → returns snapshot name string
            return "snap-001"
        if n == 4:
            # _modify → no return value needed
            return None
        # _poll iterations — return (status, version) tuple
        return ("available", "7.1.0")

    # Patch asyncio.sleep to avoid actual waiting
    with patch("app.connectors.executors.aws.elasticache_redis_upgrade._run", side_effect=fake_run):
        with patch("asyncio.sleep", new=AsyncMock()):
            from app.connectors.executors.aws.elasticache_redis_upgrade import execute
            result = await execute(
                {"replication_group_id": "my-cluster", "target_version": "7.1.0"},
                [],
                connector,
            )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "6.2.7"
    assert result["current_version"] == "7.1.0"
    assert result["snapshot_id"] == "snap-001"


async def test_rollback_returns_partial(connector):
    from app.connectors.executors.aws.elasticache_redis_upgrade import rollback
    result = await rollback(
        {"replication_group_id": "my-cluster"},
        {"snapshot_id": "snap-001", "previous_version": "6.2.7"},
        connector,
    )
    assert result["rolled_back"] is False
    assert "snap-001" in result["reason"]
    assert result["snapshot_id"] == "snap-001"
