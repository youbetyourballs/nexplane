# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.mark.asyncio
async def test_terraform_discover_workspaces_mock():
    from app.connectors.executors.terraform.discover_workspaces import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_workspaces"
    assert isinstance(result["workspaces"], list)


@pytest.mark.asyncio
async def test_terraform_lock_rollback():
    from app.connectors.executors.terraform.lock_workspace import rollback
    result = await rollback({"workspace_id": "ws-test"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "unlock_workspace"


@pytest.mark.asyncio
async def test_checkov_scan_iac_mock():
    from app.connectors.executors.checkov.scan_iac import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "scan_iac"
    assert "findings" in result


@pytest.mark.asyncio
async def test_saltstack_run_function_allowlist():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "rm -rf /", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
    assert "allowlist" in result["error"]


@pytest.mark.asyncio
async def test_saltstack_run_function_allowed():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "test.ping", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "run_function"
    assert result["function"] == "test.ping"
