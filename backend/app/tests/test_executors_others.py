# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

﻿import pytest
from app.connectors.executors.okta import suspend_user, unsuspend_user, deactivate_user, revoke_sessions, reset_mfa_factors
from app.connectors.executors.ssh import validate_template, execute_template, collect_output, install_agent, uninstall_agent, check_prerequisites, download_package, start_service
from app.connectors.executors.paloalto import analyze_flows, generate_diff, stage_policy, remove_staged_policy, validate_staged


@pytest.mark.asyncio
async def test_okta_suspend_user_returns_suspended():
    result = await suspend_user.execute({"user_id": "test-user"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "suspend_user"
    assert result["user_id"] == "test-user"


@pytest.mark.asyncio
async def test_okta_suspend_rollback_returns_not_rolled_back():
    result = await suspend_user.rollback({"user_id": "test-user"}, {}, type("C", (), {"credentials": {}})())
    assert result["rolled_back"] is False


@pytest.mark.asyncio
async def test_execute_template_approved_succeeds():
    result = await execute_template.execute(
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"], None
    )
    assert result["action"] == "execute_template"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_execute_template_unapproved_raises():
    with pytest.raises(ValueError, match="not approved"):
        await execute_template.execute(
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_execute_template_freeform_raises():
    with pytest.raises(ValueError, match="[Ff]reeform"):
        await execute_template.execute(
            {"template_id": "restart_service", "freeform_command": "rm -rf /"},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_stage_policy_simulation_mode():
    result = await stage_policy.execute(
        {"policy_rules": [{"src": "web", "dst": "api", "port": 443}], "critical_flows": []},
        ["fw-1"], None
    )
    assert result["mode"] == "simulation"
    assert result["staged_policy_id"].startswith("pol-")


@pytest.mark.asyncio
async def test_stage_policy_rollback_removes():
    result = await stage_policy.rollback({}, {"staged_policy_id": "pol-abc123"}, None)
    assert result["rolled_back"] is True
    assert result["policy_id"] == "pol-abc123"


