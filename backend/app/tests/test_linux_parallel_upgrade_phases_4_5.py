# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_params(**overrides):
    base = {
        "source_asset_id": str(uuid.uuid4()),
        "dest_asset_id": str(uuid.uuid4()),
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "health_check_ports": [8080],
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 0,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _make_connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


@pytest.mark.asyncio
async def test_health_check_port_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    params = _make_params()

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 1.0}))
    # Simulate port probe failure: port 8080 closed
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=False))
    # Speed up the retry loop for tests
    monkeypatch.setattr(lpu, "_HEALTH_CHECK_RETRY_SLEEP", 0)
    monkeypatch.setattr(lpu, "_HEALTH_CHECK_TIMEOUT_SECONDS", 0.1)

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "8080" in result["error"] or "port" in result["error"].lower()


@pytest.mark.asyncio
async def test_health_check_command_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    params = _make_params(health_check_ports=[], health_check_command="curl -sf http://localhost/health")

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            # health_check_command fails
            return {"output": "connection refused", "exit_code": 1}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 1.0}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "health" in result["error"].lower() or "exit" in result["error"].lower()


@pytest.mark.asyncio
async def test_cutover_records_checkpoint(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    params = _make_params()

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            if "hostname -I" in parameters.get("command", ""):
                return {"output": "10.0.0.2", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_phase2_snapshot", AsyncMock(return_value={"snapshot_id": "snap-y", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 100, "files_transferred": 1, "duration_seconds": 0.5}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is True
    assert result["source_stopped"] is True
    assert result["snapshot_id"] == "snap-y"
    assert result["cutover_method"] == "eip"
    assert result["cutover_config"] == {"eip_allocation_id": "eipalloc-abc123"}
    assert result.get("error") is None
