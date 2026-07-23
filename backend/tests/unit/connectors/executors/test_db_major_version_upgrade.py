# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: default port
# ---------------------------------------------------------------------------

def test_default_port_postgres():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _default_port
    assert _default_port("postgres") == 5432


def test_default_port_mysql():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _default_port
    assert _default_port("mysql") == 3306


def test_default_port_mongodb():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _default_port
    assert _default_port("mongodb") == 27017


# ---------------------------------------------------------------------------
# Helper: MongoDB FCV chain computation
# ---------------------------------------------------------------------------

def test_mongo_fcv_chain_44_to_70():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _compute_mongo_fcv_chain
    chain = _compute_mongo_fcv_chain("4.4", "7.0")
    assert chain == ["4.4", "5.0", "6.0", "7.0"]


def test_mongo_fcv_chain_50_to_70():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _compute_mongo_fcv_chain
    chain = _compute_mongo_fcv_chain("5.0", "7.0")
    assert chain == ["5.0", "6.0", "7.0"]


def test_mongo_fcv_chain_60_to_70():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _compute_mongo_fcv_chain
    chain = _compute_mongo_fcv_chain("6.0", "7.0")
    assert chain == ["6.0", "7.0"]


def test_mongo_fcv_chain_invalid_raises():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _compute_mongo_fcv_chain
    with pytest.raises(ValueError, match="not in supported MongoDB versions"):
        _compute_mongo_fcv_chain("3.6", "7.0")


def test_mongo_fcv_chain_target_not_in_sequence_raises():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _compute_mongo_fcv_chain
    with pytest.raises(ValueError):
        _compute_mongo_fcv_chain("4.4", "8.0")


# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

def test_rollback_capability_is_full():
    import app.connectors.executors.nexplane_agent.db_major_version_upgrade as m
    assert m.ROLLBACK_CAPABILITY == "full"


# ---------------------------------------------------------------------------
# dry_run: preflight only, no snapshot, no upgrade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_returns_preflight_only():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import execute

    mock_dispatch = AsyncMock(return_value={
        "status": "preflight_passed",
        "engine": "postgres",
        "source_version": "12",
        "target_version": "16",
        "checks": [{"name": "disk_space", "level": "ok", "detail": "100GB free"}],
        "blocking_checks": [],
    })
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await execute(
            {"engine": "postgres", "target_version": "16", "dry_run": True},
            ["asset-abc"],
            MagicMock(),
        )
    assert result["status"] == "preflight_passed"
    assert "checks" in result
    # dispatch called once (preflight) — NOT called for snapshot or upgrade
    assert mock_dispatch.call_count == 1
    assert mock_dispatch.call_args[1]["command"] == "db_preflight"


# ---------------------------------------------------------------------------
# preflight_blocked: critical check blocks execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_critical_blocks_execution():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import execute

    mock_dispatch = AsyncMock(return_value={
        "status": "preflight_blocked",
        "engine": "postgres",
        "source_version": "12",
        "target_version": "16",
        "checks": [
            {"name": "pg_upgrade_binary", "level": "critical", "detail": "not found"}
        ],
        "blocking_checks": ["pg_upgrade_binary"],
    })
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await execute(
            {"engine": "postgres", "target_version": "16", "strategy": "in_place"},
            ["asset-abc"],
            MagicMock(),
        )
    assert result["status"] == "preflight_blocked"
    assert "pg_upgrade_binary" in result["blocking_checks"]
    # Only preflight was called — no snapshot, no upgrade
    assert mock_dispatch.call_count == 1


# ---------------------------------------------------------------------------
# Snapshot phase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_take_snapshot_rds_calls_boto3():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _take_snapshot

    mock_rds = MagicMock()
    mock_rds.create_db_snapshot.return_value = {
        "DBSnapshot": {"DBSnapshotArn": "arn:aws:rds:us-east-1:123:snapshot:nexplane-preupgrade-abc12"}
    }
    mock_rds.get_waiter.return_value.wait = MagicMock()

    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._boto3_rds",
        return_value=mock_rds,
    ):
        result = await _take_snapshot(
            "asset-1",
            {
                "engine": "postgres",
                "rds_instance_id": "mydb",
                "snapshot_s3_bucket": None,
                "strategy": "dump_restore",
            },
            MagicMock(credentials={"access_key_id": "k", "secret_access_key": "s", "region": "us-east-1"}),
            cr_id="cr-abcdef12",
        )
    assert result["snapshot_type"] == "rds_snapshot"
    assert "snapshot_arn" in result
    mock_rds.create_db_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_take_snapshot_s3_dump_dispatches_agent():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _take_snapshot

    mock_dispatch = AsyncMock(return_value={"exit_code": 0, "stdout": ""})
    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {"ContentLength": 1024}

    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._boto3_s3",
        return_value=mock_s3,
    ):
        result = await _take_snapshot(
            "asset-1",
            {
                "engine": "postgres",
                "rds_instance_id": None,
                "snapshot_s3_bucket": "my-bucket",
                "strategy": "dump_restore",
                "db_user": "postgres",
                "db_password": "pw",
                "db_port": 5432,
            },
            MagicMock(credentials={"access_key_id": "k", "secret_access_key": "s", "region": "us-east-1"}),
            cr_id="cr-abcdef12",
        )
    assert result["snapshot_type"] == "s3_dump"
    assert "snapshot_s3_key" in result
    mock_dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_skip_snapshot_blocked_on_prod(monkeypatch):
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import execute

    # Mock preflight to pass
    mock_dispatch = AsyncMock(return_value={
        "status": "preflight_passed",
        "checks": [],
        "blocking_checks": [],
    })

    # Mock asset with environment=prod
    mock_asset = MagicMock()
    mock_asset.asset_metadata = {"environment": "prod"}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=mock_asset)

    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ):
        with pytest.raises(RuntimeError, match="skip_snapshot.*prod"):
            await execute(
                {"engine": "postgres", "target_version": "16", "skip_snapshot": True},
                ["asset-1"],
                MagicMock(),
            )


# ---------------------------------------------------------------------------
# Upgrade phase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upgrade_postgres_dump_restore_dispatches_correct_command():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _upgrade

    mock_dispatch = AsyncMock(return_value={"exit_code": 0, "stdout": "upgrade ok"})
    p = {
        "engine": "postgres",
        "strategy": "dump_restore",
        "source_version": "12",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _upgrade("asset-1", p, MagicMock())
    assert result["upgrade_status"] == "completed"
    call_args = mock_dispatch.call_args[1]
    assert call_args["command"] == "db_upgrade_postgres_dump_restore"


@pytest.mark.asyncio
async def test_upgrade_postgres_in_place_dispatches_correct_command():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _upgrade

    mock_dispatch = AsyncMock(return_value={"exit_code": 0, "stdout": "pg_upgrade ok"})
    p = {
        "engine": "postgres",
        "strategy": "in_place",
        "source_version": "12",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _upgrade("asset-1", p, MagicMock())
    assert result["upgrade_status"] == "completed"
    call_args = mock_dispatch.call_args[1]
    assert call_args["command"] == "db_upgrade_postgres_in_place"


@pytest.mark.asyncio
async def test_upgrade_mysql_dispatches_correct_command():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _upgrade

    mock_dispatch = AsyncMock(return_value={"exit_code": 0, "stdout": "mysql upgrade ok"})
    p = {
        "engine": "mysql",
        "strategy": "in_place",
        "source_version": "5.7",
        "target_version": "8.0",
        "db_user": "root",
        "db_port": 3306,
        "db_password": "pw",
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _upgrade("asset-1", p, MagicMock())
    assert result["upgrade_status"] == "completed"
    call_args = mock_dispatch.call_args[1]
    assert call_args["command"] == "db_upgrade_mysql"


@pytest.mark.asyncio
async def test_upgrade_mongodb_fcv_chain_completes_all_hops():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _upgrade

    dispatch_calls = []

    async def mock_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append(command)
        return {"exit_code": 0, "stdout": f"{command} ok"}

    p = {
        "engine": "mongodb",
        "strategy": "in_place",
        "source_version": "4.4",
        "target_version": "7.0",
        "db_user": "admin",
        "db_port": 27017,
        "db_password": "pw",
        "mongo_fcv_chain": None,  # auto-compute
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _upgrade("asset-1", p, MagicMock())

    assert result["upgrade_status"] == "completed"
    assert result["completed_hops"] == ["4.4->5.0", "5.0->6.0", "6.0->7.0"]
    # One dispatch call per hop
    mongo_calls = [c for c in dispatch_calls if "mongo" in c]
    assert len(mongo_calls) == 3


@pytest.mark.asyncio
async def test_upgrade_mongodb_partial_failure_records_completed_hops():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _upgrade

    hop_count = [0]

    async def mock_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        if "mongo" in command:
            hop_count[0] += 1
            if hop_count[0] == 2:
                raise RuntimeError("FCV bump failed at 6.0")
        return {"exit_code": 0, "stdout": "ok"}

    p = {
        "engine": "mongodb",
        "strategy": "in_place",
        "source_version": "4.4",
        "target_version": "7.0",
        "db_user": "admin",
        "db_port": 27017,
        "db_password": "pw",
        "mongo_fcv_chain": None,
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        with pytest.raises(RuntimeError, match="FCV bump failed"):
            await _upgrade("asset-1", p, MagicMock())


# ---------------------------------------------------------------------------
# Verify phase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_passes_when_version_matches():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _verify

    mock_dispatch = AsyncMock(return_value={
        "stdout": "PostgreSQL 16.3 on x86_64-pc-linux-gnu",
        "exit_code": 0,
    })
    p = {
        "engine": "postgres",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
        "health_check_url": None,
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _verify("asset-1", p, MagicMock())
    assert result["verify_status"] == "passed"
    assert result["smoke_query_ok"] is True
    assert "16" in result.get("db_version_confirmed", "")


@pytest.mark.asyncio
async def test_verify_fails_when_version_mismatch():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _verify

    mock_dispatch = AsyncMock(return_value={
        "stdout": "PostgreSQL 12.8 on x86_64-pc-linux-gnu",
        "exit_code": 0,
    })
    p = {
        "engine": "postgres",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
        "health_check_url": None,
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await _verify("asset-1", p, MagicMock())
    assert result["verify_status"] == "failed"
    assert result["smoke_query_ok"] is False


@pytest.mark.asyncio
async def test_verify_health_check_url_retries_on_5xx():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _verify

    mock_dispatch = AsyncMock(return_value={
        "stdout": "PostgreSQL 16.3", "exit_code": 0,
    })

    call_count = [0]

    class MockResponse:
        def __init__(self, status):
            self.status_code = status

    async def mock_get(url, timeout):
        call_count[0] += 1
        if call_count[0] < 3:
            return MockResponse(503)
        return MockResponse(200)

    p = {
        "engine": "postgres",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
        "health_check_url": "https://app.internal/health",
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._http_get",
        mock_get,
    ):
        result = await _verify("asset-1", p, MagicMock())
    assert result["verify_status"] == "passed"
    assert result["health_check_status"] == 200
    assert result["health_check_attempts"] == 3
    assert call_count[0] == 3


@pytest.mark.asyncio
async def test_verify_health_check_url_fails_after_3_attempts():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import _verify

    mock_dispatch = AsyncMock(return_value={
        "stdout": "PostgreSQL 16.3", "exit_code": 0,
    })

    async def mock_get(url, timeout):
        class R:
            status_code = 503
        return R()

    p = {
        "engine": "postgres",
        "target_version": "16",
        "db_user": "postgres",
        "db_port": 5432,
        "db_password": "pw",
        "health_check_url": "https://app.internal/health",
    }
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
        mock_dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._http_get",
        mock_get,
    ):
        result = await _verify("asset-1", p, MagicMock())
    assert result["verify_status"] == "failed"
    assert result["health_check_attempts"] == 3


# ---------------------------------------------------------------------------
# Rollback phase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_no_snapshot_returns_false():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import rollback
    result = await rollback({}, {}, None)
    assert result["rolled_back"] is False
    assert result["reason"] == "no_snapshot_available"


@pytest.mark.asyncio
async def test_rollback_no_snapshot_skipped_returns_false():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import rollback
    result = await rollback(
        {},
        {"snapshot_result": {"snapshot_type": "skipped", "snapshot_id": None}},
        None,
    )
    assert result["rolled_back"] is False
    assert result["reason"] == "no_snapshot_available"


@pytest.mark.asyncio
async def test_rollback_dispatches_ebs_restore_for_in_place():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import rollback

    mock_ebs_rollback = AsyncMock(return_value={
        "rolled_back": True,
        "strategy": "ebs_volume_swap",
        "new_volume_id": "vol-new",
        "old_volume_id": "vol-old",
        "agent_recovered": True,
    })
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._rollback_ebs",
        mock_ebs_rollback,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {
                "engine": "postgres",
                "asset_id": "asset-1",
                "snapshot_result": {
                    "snapshot_type": "ebs_snapshot",
                    "snapshot_id": "snap-abc123",
                    "snapshot_meta": {
                        "snapshot_id": "snap-abc123",
                        "root_volume_id": "vol-old",
                        "root_device_name": "/dev/xvda",
                        "availability_zone": "us-east-1a",
                        "region": "us-east-1",
                        "instance_id": "i-test",
                    },
                },
            },
            MagicMock(),
        )
    assert result["rolled_back"] is True
    assert result["strategy"] == "ebs_volume_swap"
    mock_ebs_rollback.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_dispatches_s3_restore_for_dump_restore():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import rollback

    mock_s3_rollback = AsyncMock(return_value={
        "rolled_back": True,
        "strategy": "s3_dump_restore",
        "snapshot_s3_key": "nexplane-abc12-20260723T120000Z.dump.gz",
    })
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._rollback_s3_dump",
        mock_s3_rollback,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {
                "engine": "postgres",
                "asset_id": "asset-1",
                "snapshot_result": {
                    "snapshot_type": "s3_dump",
                    "snapshot_id": "nexplane-abc12-20260723T120000Z.dump.gz",
                    "snapshot_s3_key": "nexplane-abc12-20260723T120000Z.dump.gz",
                    "snapshot_s3_bucket": "nexplane-db-snapshots",
                },
            },
            MagicMock(),
        )
    assert result["rolled_back"] is True
    assert result["strategy"] == "s3_dump_restore"
    mock_s3_rollback.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_failure_returns_false_with_reason():
    from app.connectors.executors.nexplane_agent.db_major_version_upgrade import rollback

    mock_ebs_rollback = AsyncMock(side_effect=RuntimeError("EBS API timeout"))
    with patch(
        "app.connectors.executors.nexplane_agent.db_major_version_upgrade._rollback_ebs",
        mock_ebs_rollback,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {
                "engine": "postgres",
                "asset_id": "asset-1",
                "snapshot_result": {
                    "snapshot_type": "ebs_snapshot",
                    "snapshot_id": "snap-abc123",
                    "snapshot_meta": {"instance_id": "i-test"},
                },
            },
            MagicMock(),
        )
    assert result["rolled_back"] is False
    assert "EBS API timeout" in result["reason"]
