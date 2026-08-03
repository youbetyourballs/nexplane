# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for the ssh_ca_rotation executor (mock path — no agent infrastructure)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

MODULE = "app.connectors.executors.nexplane_agent.ssh_ca_rotation"


def _mock_connector():
    return MagicMock()


@pytest.mark.asyncio
async def test_rollback_capability_constant():
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import ROLLBACK_CAPABILITY
    assert ROLLBACK_CAPABILITY == "full"


@pytest.mark.asyncio
async def test_execute_mock_returns_structured_result():
    """execute() with empty asset_ids returns structured mock result without hitting agent."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute
    result = await execute({}, [], _mock_connector())
    assert "phases" in result
    assert "new_ca_fingerprint" in result
    assert "hosts_updated" in result
    assert result["status"] == "mock"


@pytest.mark.asyncio
async def test_empty_asset_ids_returns_mock():
    """execute() returns gracefully (not raise) when asset_ids is empty."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute
    result = await execute({}, [], _mock_connector())
    assert isinstance(result, dict)
    assert result.get("_asset_ids") == []


@pytest.mark.asyncio
async def test_phase_names_present():
    """Mock result contains all 7 phase names."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute
    result = await execute({}, [], _mock_connector())
    expected_phases = {"preflight", "snapshot", "generate", "distribute", "verify", "revoke", "report"}
    assert set(result["phases"]) == expected_phases


@pytest.mark.asyncio
async def test_parameters_defaults():
    """Default parameter values are applied when no parameters provided."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute
    result = await execute({}, [], _mock_connector())
    assert result["ca_key_path"] == "/etc/ssh/nexplane_ca"
    assert result["trusted_user_ca_keys_path"] == "/etc/ssh/trusted_user_ca_keys"


@pytest.mark.asyncio
async def test_parameters_custom_paths():
    """Custom ca_key_path and trusted_user_ca_keys_path are reflected in mock result."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute
    result = await execute(
        {"ca_key_path": "/opt/ca/myca", "trusted_user_ca_keys_path": "/opt/ca/trusted_keys"},
        [],
        _mock_connector(),
    )
    assert result["ca_key_path"] == "/opt/ca/myca"
    assert result["trusted_user_ca_keys_path"] == "/opt/ca/trusted_keys"


@pytest.mark.asyncio
async def test_rollback_mock():
    """rollback() with empty _asset_ids returns rolled_back True without hitting agent."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import rollback
    execution_result = {
        "_asset_ids": [],
        "old_ca_snapshots": {},
        "trusted_user_ca_keys_path": "/etc/ssh/trusted_user_ca_keys",
    }
    result = await rollback({}, execution_result, _mock_connector())
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_execute_with_assets_dispatches_run_command():
    """execute() with real asset_ids dispatches run_command jobs via dispatch_agent_job."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import execute

    run_command_calls = []

    async def mock_dispatch(command, parameters, asset_ids, timeout_seconds=60):
        run_command_calls.append((command, parameters.get("command", ""), asset_ids))
        cmd = parameters.get("command", "")
        if "cat" in cmd and "nexplane_ca.pub" in cmd:
            return {"stdout": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA nexplane-ca-20250101000000"}
        if "grep" in cmd:
            return {"stdout": "FOUND"}
        return {"stdout": "OK"}

    with patch(f"{MODULE}._dispatch.dispatch_agent_job", side_effect=mock_dispatch):
        result = await execute(
            {"ca_key_path": "/etc/ssh/nexplane_ca"},
            ["asset-uuid-1"],
            _mock_connector(),
        )

    assert result["status"] == "completed"
    assert "asset-uuid-1" in result["hosts_updated"]
    dispatched_commands = [c[0] for c in run_command_calls]
    assert "run_command" in dispatched_commands


@pytest.mark.asyncio
async def test_rollback_with_assets_dispatches_restore():
    """rollback() dispatches run_command to restore TrustedUserCAKeys on each host."""
    from app.connectors.executors.nexplane_agent.ssh_ca_rotation import rollback

    dispatch_calls = []

    async def mock_dispatch(command, parameters, asset_ids, timeout_seconds=60):
        dispatch_calls.append((command, parameters, asset_ids))
        return {"stdout": "RESTORED"}

    execution_result = {
        "_asset_ids": ["asset-uuid-1"],
        "old_ca_snapshots": {"asset-uuid-1": "ssh-rsa AAAA old-ca-comment"},
        "trusted_user_ca_keys_path": "/etc/ssh/trusted_user_ca_keys",
    }

    with patch(f"{MODULE}._dispatch.dispatch_agent_job", side_effect=mock_dispatch):
        result = await rollback({}, execution_result, _mock_connector())

    assert result["rolled_back"] is True
    assert "asset-uuid-1" in result["hosts_restored"]
    assert len(dispatch_calls) >= 1
