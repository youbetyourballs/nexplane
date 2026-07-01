# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_configure_ebpf_network_rollback_passes_snapshot_id():
    from app.connectors.executors.nexplane_agent import configure_ebpf_network
    execution_result = {
        "_asset_ids": ["asset-1"],
        "snapshot_id": "snap-abc123",
        "policy_type": "network",
    }
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"rolled_back": True}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await configure_ebpf_network.rollback({}, execution_result, connector=None)

    assert captured["command"] == "configure_ebpf_network"
    assert captured["parameters"]["action"] == "restore"
    assert captured["parameters"]["snapshot_id"] == "snap-abc123"
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_configure_ebpf_lsm_rollback_passes_snapshot_id():
    from app.connectors.executors.nexplane_agent import configure_ebpf_lsm
    execution_result = {
        "_asset_ids": ["asset-1"],
        "snapshot_id": "snap-def456",
        "kernel_lsm": True,
    }
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"rolled_back": True}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await configure_ebpf_lsm.rollback({}, execution_result, connector=None)

    assert captured["command"] == "configure_ebpf_lsm"
    assert captured["parameters"]["action"] == "restore"
    assert captured["parameters"]["snapshot_id"] == "snap-def456"
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_promote_ebpf_policy_dispatches_correct_command():
    from app.connectors.executors.nexplane_agent import promote_ebpf_policy
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"prior_mode": "audit", "current_mode": "enforce"}

    params = {"policy_type": "network", "asset_id": "asset-1"}
    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await promote_ebpf_policy.execute(params, ["asset-1"], connector=None)

    assert captured["command"] == "promote_ebpf_policy"
    assert captured["parameters"]["policy_type"] == "network"
    assert result["prior_mode"] == "audit"


@pytest.mark.asyncio
async def test_promote_ebpf_policy_rollback_restores_audit():
    from app.connectors.executors.nexplane_agent import promote_ebpf_policy
    execution_result = {"_asset_ids": ["asset-1"], "prior_mode": "audit", "policy_type": "network"}
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"prior_mode": "enforce", "current_mode": "audit"}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await promote_ebpf_policy.rollback({}, execution_result, connector=None)

    assert captured["parameters"]["mode"] == "audit"
    assert captured["parameters"]["policy_type"] == "network"
