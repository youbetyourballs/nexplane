# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.mark.asyncio
async def test_jira_create_issue_mock():
    from app.connectors.executors.jira.create_issue import execute
    result = await execute({"project_key": "SEC", "issue_type": "Bug", "summary": "Test issue"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "create_issue"
    assert "issue_key" in result


@pytest.mark.asyncio
async def test_pagerduty_create_incident_rollback():
    from app.connectors.executors.pagerduty.create_incident import rollback
    result = await rollback({"service_id": "P001"}, {"incident_id": "Q001"}, type("C", (), {"credentials": {}})())
    assert result["action"] == "resolve_incident"


@pytest.mark.asyncio
async def test_datadog_discover_hosts_mock():
    from app.connectors.executors.datadog.discover_hosts import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_hosts"


@pytest.mark.asyncio
async def test_google_workspace_wipe_device_requires_confirm():
    from app.connectors.executors.google_workspace.wipe_device import execute
    result = await execute({"resource_id": "mock-device"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
    assert "confirm_wipe" in result["error"]


@pytest.mark.asyncio
async def test_saltstack_function_not_in_allowlist():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "cmd.run", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
