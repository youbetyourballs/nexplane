# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

﻿import pytest
from app.services.connector_service import execute_action, run_preflight_checks, run_verification_checks, ConnectorError


@pytest.mark.asyncio
async def test_dns_update_returns_previous_value():
    result = await execute_action(
        "cloudflare", "update_dns_record",
        {"record_name": "api.example.com", "record_type": "A", "new_value": "1.2.3.4", "ttl": 300},
        ["asset-1"],
    )
    assert result["action"] == "update_dns_record"
    assert result["previous_value"] is not None
    assert result["new_value"] == "1.2.3.4"
    assert "propagation_id" in result


@pytest.mark.asyncio
async def test_snapshot_returns_snapshot_ids():
    result = await execute_action(
        "aws", "create_snapshot",
        {"snapshot_tag": "test"},
        ["asset-1", "asset-2"],
    )
    assert result["action"] == "create_snapshot"
    assert len(result["snapshots"]) == 2
    assert all(s["snapshot_id"].startswith("snap-") for s in result["snapshots"])


@pytest.mark.asyncio
async def test_remote_command_approved_template_succeeds():
    result = await execute_action(
        "ssh", "execute_template",
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"],
    )
    assert result["action"] == "execute_template"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_remote_command_unapproved_template_raises():
    with pytest.raises((ValueError, ConnectorError)):
        await execute_action(
            "ssh", "execute_template",
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"],
        )


@pytest.mark.asyncio
async def test_microsegmentation_staged_only():
    result = await execute_action(
        "paloalto", "stage_policy",
        {"policy_rules": [{"src": "a", "dst": "b", "port": 443}]},
        ["asset-1"],
    )
    assert result["mode"] == "simulation"
    assert "staged_policy_id" in result


@pytest.mark.asyncio
async def test_unknown_connector_raises():
    with pytest.raises((KeyError, ImportError, ConnectorError)):
        await execute_action("nonexistent_connector", "some_action", {}, [])


@pytest.mark.asyncio
async def test_preflight_all_pass():
    checks = [{"name": "check_1", "description": "test", "check_type": "connectivity", "expected_result": "ok"}]
    result = await run_preflight_checks(checks)
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verification_passes_for_mock():
    plan = {"checks": [{"name": "dns_resolves", "description": "DNS check", "method": "dns_lookup"}], "success_criteria": "DNS resolves"}
    result = await run_verification_checks(plan, {"action": "update_dns_record"})
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_rollback_dns_via_execute_action():
    result = await execute_action(
        "cloudflare", "restore_dns_record",
        {"record_name": "api.example.com", "previous_value": "10.0.0.1"},
        [],
    )
    assert result["restored_value"] == "10.0.0.1"


