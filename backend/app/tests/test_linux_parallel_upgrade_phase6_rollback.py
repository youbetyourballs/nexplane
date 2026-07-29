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


def _make_connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


@pytest.mark.asyncio
async def test_decommission_job_scheduled(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    scheduled_jobs = []

    class FakeScheduler:
        def add_job(self, fn, trigger, id, **kwargs):
            scheduled_jobs.append({"id": id, "trigger": trigger})
            return MagicMock(id=id)

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: FakeScheduler())
    params = _make_params(decommission_after_hours=24)
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": "snap-abc",
        "decommission_job_id": None,
        "decommission_manual": False,
    }
    result = await lpu._phase6_decommission(params, execution_result, _make_connector())
    assert result["decommission_job_id"] is not None
    assert result["decommission_manual"] is False
    assert len(scheduled_jobs) == 1


@pytest.mark.asyncio
async def test_manual_decommission_no_job(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    params = _make_params(decommission_after_hours=0)
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": None,
        "decommission_job_id": None,
        "decommission_manual": False,
    }
    result = await lpu._phase6_decommission(params, execution_result, _make_connector())
    assert result["decommission_manual"] is True
    assert result["decommission_job_id"] is None


@pytest.mark.asyncio
async def test_rollback_pre_cutover_no_traffic_change(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    reverse_calls = []
    monkeypatch.setattr(lpu, "_do_cutover", AsyncMock(side_effect=lambda *a, **kw: reverse_calls.append(1)))

    params = _make_params()
    execution_result = {
        "cutover_completed": False,
        "source_stopped": False,
        "snapshot_id": None,
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert len(reverse_calls) == 0
    assert result.get("error") is None


@pytest.mark.asyncio
async def test_rollback_post_cutover_reverses_traffic(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    reverse_calls = []

    async def _mock_do_cutover(src, dst, params, connector, reverse=False):
        reverse_calls.append(reverse)

    async def _mock_start_source(asset_id, connector):
        pass

    monkeypatch.setattr(lpu, "_do_cutover", _mock_do_cutover)
    monkeypatch.setattr(lpu, "_start_source", _mock_start_source)
    monkeypatch.setattr(lpu, "_check_agent", AsyncMock())

    params = _make_params()
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": "snap-abc",
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert True in reverse_calls
    assert result.get("error") is None
