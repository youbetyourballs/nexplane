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
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 24,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _make_connector(is_ec2=True):
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"} if is_ec2 else {}
    return c


@pytest.mark.asyncio
async def test_preflight_fails_if_dest_os_older(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    params = _make_params()

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                # both source and dest return same version — dest not newer, should fail
                return {"output": "20.04", "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "100M\t/var/lib/testapp", "exit_code": 0}
            if "df" in cmd:
                return {"output": "50G available", "exit_code": 0}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["error"] is not None
    assert "version" in result["error"].lower()


@pytest.mark.asyncio
async def test_preflight_fails_if_agent_unreachable(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            raise RuntimeError("agent timeout")
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)

    params = _make_params()
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["error"] is not None
    assert "unreachable" in result["error"].lower() or "timeout" in result["error"].lower()


@pytest.mark.asyncio
async def test_dry_run_stops_after_preflight(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    params = _make_params(dry_run=True)
    dispatch_calls = []

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        dispatch_calls.append(command)
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                # source gets 20.04, dest gets 22.04
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "100M\t/var/lib/testapp", "exit_code": 0}
            if "df" in cmd:
                return {"output": "50G available", "exit_code": 0}
            if "test -e" in cmd or "test -d" in cmd:
                return {"output": "ok", "exit_code": 0}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-abc"}))

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result.get("dry_run") is True
    assert result.get("error") is None
    assert "preflight" in result
    # _take_snapshot must NOT have been called
    lpu._take_snapshot.assert_not_called()
