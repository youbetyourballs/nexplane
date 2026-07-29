# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock


def _params(**overrides):
    src = str(uuid.uuid4())
    dst = str(uuid.uuid4())
    base = {
        "source_asset_id": src,
        "dest_asset_id": dst,
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "health_check_ports": [],
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 24,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _conn_ec2():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


def _conn_onprem():
    c = MagicMock()
    c.credentials = {}
    return c


def _mock_dispatch_versions(src_id, src_ver="20.04", dst_ver="22.04"):
    """Return an async dispatch mock that answers version queries."""
    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                return {"output": src_ver if asset_ids[0] == src_id else dst_ver, "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "10M\t/var/lib/testapp", "exit_code": 0}
            if "test -e" in cmd:
                return {"output": "ok", "exit_code": 0}
            if "hostname -I" in cmd:
                return {"output": "10.0.0.5", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}
    return _dispatch


# 1. test_preflight_fails_if_dest_os_older
@pytest.mark.asyncio
async def test_preflight_fails_if_dest_os_older(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params()
    # Both source and dest report 20.04 — dest not strictly greater
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"], "20.04", "20.04"))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["error"] is not None
    assert result["cutover_completed"] is False


# 2. test_preflight_fails_if_agent_unreachable
@pytest.mark.asyncio
async def test_preflight_fails_if_agent_unreachable(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params()

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            raise RuntimeError("connection timeout")
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["error"] is not None
    assert result["cutover_completed"] is False


# 3. test_dry_run_stops_after_preflight
@pytest.mark.asyncio
async def test_dry_run_stops_after_preflight(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(dry_run=True)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    snap_mock = AsyncMock(return_value={"snapshot_id": "snap-x"})
    monkeypatch.setattr(lpu, "_take_snapshot", snap_mock)
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["dry_run"] is True
    assert result["error"] is None
    snap_mock.assert_not_called()
    assert result["cutover_completed"] is False


# 4. test_pre_sync_runs_respected
@pytest.mark.asyncio
async def test_pre_sync_runs_respected(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(pre_sync_runs=3, decommission_after_hours=0)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    rsync_mock = AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1})
    monkeypatch.setattr(lpu, "_run_rsync", rsync_mock)
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    # pre_sync_runs=3 → 2 pre-sync runs (Phase 3) + 1 final run (Phase 5) = 3 total
    assert rsync_mock.call_count == 3


# 5. test_health_check_port_failure_blocks_cutover
@pytest.mark.asyncio
async def test_health_check_port_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(health_check_ports=[8080])
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=False))
    # Override retry sleep and timeout so the test doesn't wait 30 seconds
    monkeypatch.setattr(lpu, "_HEALTH_CHECK_RETRY_SLEEP", 0)
    monkeypatch.setattr(lpu, "_HEALTH_CHECK_TIMEOUT_SECONDS", 0)
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "8080" in result["error"] or "port" in result["error"].lower()


# 6. test_health_check_command_failure_blocks_cutover
@pytest.mark.asyncio
async def test_health_check_command_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(health_check_command="curl -sf http://localhost/health")

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                return {"output": "20.04" if asset_ids[0] == params["source_asset_id"] else "22.04", "exit_code": 0}
            if "hostname -I" in cmd:
                return {"output": "10.0.0.5", "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "10M\t/var/lib/testapp", "exit_code": 0}
            if "test -e" in cmd:
                return {"output": "ok", "exit_code": 0}
            # health_check_command fails
            return {"output": "connection refused", "exit_code": 1}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is False
    assert result["error"] is not None


# 7. test_cutover_records_checkpoint
@pytest.mark.asyncio
async def test_cutover_records_checkpoint(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(decommission_after_hours=0)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-chk", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 100, "files_transferred": 1, "duration_seconds": 0.5}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is True
    assert result["source_stopped"] is True
    assert result["snapshot_id"] == "snap-chk"
    assert result["cutover_method"] == "eip"
    assert result["cutover_config"] == {"eip_allocation_id": "eipalloc-abc123"}
    assert result["error"] is None


# 8. test_rollback_pre_cutover_no_traffic_change
@pytest.mark.asyncio
async def test_rollback_pre_cutover_no_traffic_change(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    from app.connectors.executors.nexplane_agent.linux_parallel_upgrade import ROLLBACK_CAPABILITY_FULL
    reverse_calls = []

    async def _mock_cutover(src, dst, params, connector, reverse=False):
        reverse_calls.append(reverse)

    monkeypatch.setattr(lpu, "_do_cutover", _mock_cutover)
    params = _params()
    execution_result = {
        "cutover_completed": False,
        "source_stopped": False,
        "snapshot_id": None,
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": ROLLBACK_CAPABILITY_FULL,
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert not reverse_calls
    assert result["error"] is None


# 9. test_rollback_post_cutover_reverses_traffic
@pytest.mark.asyncio
async def test_rollback_post_cutover_reverses_traffic(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    reverse_args = []

    async def _mock_cutover(src, dst, params, connector, reverse=False):
        reverse_args.append(reverse)

    monkeypatch.setattr(lpu, "_do_cutover", _mock_cutover)
    monkeypatch.setattr(lpu, "_start_source", AsyncMock())
    monkeypatch.setattr(lpu, "_check_agent", AsyncMock())
    params = _params()
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": "snap-abc",
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert True in reverse_args
    assert result["error"] is None


# 10. test_decommission_job_scheduled
@pytest.mark.asyncio
async def test_decommission_job_scheduled(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    jobs = []

    class FakeSched:
        def add_job(self, fn, trigger, id, **kw):
            jobs.append(id)
            return MagicMock(id=id)

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: FakeSched())
    params = _params(decommission_after_hours=24)
    execution_result = {"decommission_job_id": None, "decommission_manual": False}
    result = await lpu._phase6_decommission(params, execution_result, _conn_ec2())
    assert result["decommission_job_id"] is not None
    assert len(jobs) == 1
    assert result["decommission_manual"] is False


# 11. test_manual_decommission_no_job
@pytest.mark.asyncio
async def test_manual_decommission_no_job(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    params = _params(decommission_after_hours=0)
    execution_result = {"decommission_job_id": None, "decommission_manual": False}
    result = await lpu._phase6_decommission(params, execution_result, _conn_ec2())
    assert result["decommission_manual"] is True
    assert result["decommission_job_id"] is None
