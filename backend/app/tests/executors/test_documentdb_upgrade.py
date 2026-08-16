# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"aws_access_key_id": "k", "aws_secret_access_key": "s", "region_name": "us-east-1"}
    return c


@pytest.mark.asyncio
async def test_upgrade_and_partial_rollback(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"DBClusters": [{"EngineVersion": "4.0.0", "Status": "available"}]}
        if call_count[0] == 2:
            return {"DBClusterSnapshots": [{"DBClusterSnapshotIdentifier": "snap-docdb-001",
                                            "SnapshotCreateTime": "2026-08-16"}]}
        if call_count[0] == 3:
            return {}  # modify
        return {"DBClusters": [{"EngineVersion": "5.0.0", "Status": "available"}]}

    with patch("app.connectors.executors.aws.documentdb_upgrade._run", side_effect=fake_run):
        from app.connectors.executors.aws.documentdb_upgrade import execute
        result = await execute(
            {"db_cluster_identifier": "my-docdb", "target_version": "5.0.0"},
            [], connector,
        )
    assert result["status"] == "upgraded"
    assert result["snapshot_id"] == "snap-docdb-001"

    from app.connectors.executors.aws.documentdb_upgrade import rollback
    rb = await rollback({}, result, connector)
    assert rb["rolled_back"] is False
    assert "snap-docdb-001" in rb["reason"]
