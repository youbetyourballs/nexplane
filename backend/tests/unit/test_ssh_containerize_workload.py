# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

MODULE = "app.connectors.executors.ssh.containerize_workload"


def _make_connector(responses: dict) -> MagicMock:
    """Mock SSH connector: run_command returns tuple from responses keyed by substring."""
    conn = MagicMock()
    def run_command(cmd):
        for key, val in responses.items():
            if key in cmd:
                return val
        return ("", "", 0)
    conn.run_command.side_effect = run_command
    return conn


@pytest.mark.asyncio
async def test_inplace_path_when_docker_available():
    from app.connectors.executors.ssh.containerize_workload import execute
    conn = _make_connector({
        "is-active": ("active\n", "", 0),
        "is-enabled": ("enabled\n", "", 0),
        "docker info": ("", "", 0),
        "docker build": ("", "", 0),
        "docker inspect": ("sha256:abc\n", "", 0),
        "docker run": ("ctr-123\n", "", 0),
        "ExecStart": ("/usr/bin/myapp\n", "", 0),
        "ldd": ("", "", 0),
        "ss -tlnp": ("", "", 0),
    })
    mock_dispatch = AsyncMock(return_value={"deployed": True})
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        result = await execute({"service_name": "myapp.service", "registry": "test.io"}, [], conn)
    assert result["path"] == "inplace"
    assert result["containerized"] is True
    assert result["container_id"] == "ctr-123"


@pytest.mark.asyncio
async def test_remote_path_when_deployment_target_k8s():
    from app.connectors.executors.ssh.containerize_workload import execute
    conn = _make_connector({
        "is-active": ("active\n", "", 0),
        "is-enabled": ("enabled\n", "", 0),
        "docker info": ("", "", 0),
        "docker build": ("", "", 0),
        "docker inspect": ("sha256:abc\n", "", 0),
        "docker push": ("", "", 0),
        "ExecStart": ("/usr/bin/myapp\n", "", 0),
        "ldd": ("", "", 0),
        "ss -tlnp": ("", "", 0),
    })
    mock_dispatch = AsyncMock(return_value={"deployed": True})
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        result = await execute(
            {"service_name": "myapp.service", "registry": "test.io", "deployment_target": "k8s"},
            [],
            conn,
        )
    assert result["path"] == "remote"
    assert result["containerized"] is True


@pytest.mark.asyncio
async def test_rollback_inplace_stops_container_and_restarts_service():
    from app.connectors.executors.ssh.containerize_workload import rollback
    conn = _make_connector({})
    execution_result = {
        "path": "inplace",
        "pre_state": {
            "systemd_unit": "myapp.service",
            "was_active": True,
            "was_enabled": True,
            "container_id": "ctr-123",
            "path": "inplace",
        },
    }
    mock_dispatch = AsyncMock(return_value={})
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        result = await rollback({}, execution_result, conn)
    assert result["rolled_back"] is True
    assert result["path"] == "inplace"
    calls = [str(c) for c in conn.run_command.call_args_list]
    assert any("docker stop" in c for c in calls)
    assert any("systemctl start" in c for c in calls)


@pytest.mark.asyncio
async def test_rollback_remote_does_not_touch_source():
    from app.connectors.executors.ssh.containerize_workload import rollback
    conn = MagicMock()
    mock_dispatch = AsyncMock(return_value={})
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        result = await rollback(
            {},
            {"path": "remote", "pre_state": {"systemd_unit": "myapp.service", "path": "remote"}, "deploy_result": {}},
            conn,
        )
    assert result["rolled_back"] is True
    assert result["path"] == "remote"
    conn.run_command.assert_not_called()


@pytest.mark.asyncio
async def test_missing_service_name_raises():
    from app.connectors.executors.ssh.containerize_workload import execute
    mock_dispatch = AsyncMock(return_value={})
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        with pytest.raises(ValueError, match="service_name"):
            await execute({}, [], MagicMock())


@pytest.mark.asyncio
async def test_rollback_dispatches_agent_job():
    """Rollback for k8s/remote path must dispatch containerize_workload_rollback."""
    from app.connectors.executors.ssh.containerize_workload import rollback
    conn = MagicMock()
    mock_dispatch = AsyncMock(return_value={})
    execution_result = {
        "path": "remote",
        "pre_state": {"systemd_unit": "myapp.service", "path": "remote"},
        "deploy_result": {"namespace": "prod"},
    }
    with patch(f"{MODULE}.dispatch_agent_job", mock_dispatch):
        result = await rollback({}, execution_result, conn)
    assert result["rolled_back"] is True
    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args
    assert call_kwargs.kwargs.get("command") == "containerize_workload_rollback" or \
           call_kwargs.args[0] == "containerize_workload_rollback" if call_kwargs.args else \
           call_kwargs.kwargs["command"] == "containerize_workload_rollback"
    conn.run_command.assert_not_called()
