# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from app.connectors.executors.offboard_user import build_plan, DEFINITION


def _connector(ctype, account_identifier=None):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
        "display_name": "Alice",
        "account_status": "active",
        "account_identifier": account_identifier or f"alice-{ctype}",
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "offboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_empty_connectors_still_has_report():
    """Empty discovered connectors still returns a report (Phase 6) step."""
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, [])
    assert len(steps) == 1
    assert steps[0]["action_id"] == "generate_offboarding_report"
    assert steps[0]["phase"] == 6


@pytest.mark.asyncio
async def test_build_plan_phases_ordered():
    connectors = [
        _connector("okta"),
        _connector("active_directory"),
        _connector("slack"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    phases = [s["phase"] for s in steps]
    assert phases == sorted(phases), "Steps must be ordered by phase"


@pytest.mark.asyncio
async def test_build_plan_report_is_last_and_phase_6():
    connectors = [_connector("okta"), _connector("active_directory")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    assert steps[-1]["action_id"] == "generate_offboarding_report"
    assert steps[-1]["phase"] == 6


@pytest.mark.asyncio
async def test_build_plan_verify_steps_in_phase_5():
    connectors = [
        _connector("active_directory", account_identifier="alice"),
        _connector("okta", account_identifier="okta-user-id-123"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    assert len(verify_steps) == 2
    verify_actions = [s["action_id"] for s in verify_steps]
    assert "verify_active_directory_disabled" in verify_actions
    assert "verify_okta_disabled" in verify_actions
    for vs in verify_steps:
        assert "account_identifier" in vs["parameters"]


@pytest.mark.asyncio
async def test_verify_steps_have_no_rollback():
    connectors = [_connector("active_directory", account_identifier="alice")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    for vs in verify_steps:
        assert vs["rollback_action_id"] is None


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_only_when_requested():
    connectors = [_connector("crowdstrike", account_identifier="dev1,dev2")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": False}
    steps = await build_plan(payload, connectors)
    actions = [s["action_id"] for s in steps]
    assert "isolate_crowdstrike_endpoints" not in actions


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_verify_only_when_isolate_requested():
    connectors = [_connector("crowdstrike", account_identifier="dev1")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": True}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    assert any("crowdstrike" in s["action_id"] for s in verify_steps)
