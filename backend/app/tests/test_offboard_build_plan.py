import uuid
import pytest
from app.connectors.executors.offboard_user import build_plan, DEFINITION


def _connector(ctype):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
        "display_name": "Alice",
        "account_status": "active",
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "offboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_empty_connectors():
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, [])
    # Should still include the report step
    assert len(steps) == 1
    assert steps[0]["action"] == "generate_offboarding_report"


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
async def test_build_plan_crowdstrike_only_when_requested():
    connectors = [_connector("crowdstrike")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": False}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "isolate_crowdstrike_endpoints" not in actions


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_included_when_requested():
    connectors = [_connector("crowdstrike")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": True}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "isolate_crowdstrike_endpoints" in actions


@pytest.mark.asyncio
async def test_build_plan_report_is_last():
    connectors = [_connector("okta"), _connector("active_directory")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    assert steps[-1]["action"] == "generate_offboarding_report"


@pytest.mark.asyncio
async def test_build_plan_all_identity_connectors():
    connectors = [
        _connector("active_directory"),
        _connector("okta"),
        _connector("entra_id"),
        _connector("google_workspace"),
        _connector("github"),
        _connector("slack"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "resignation"}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "disable_active_directory_account" in actions
    assert "disable_okta_account" in actions
    assert "disable_entra_id_account" in actions
    assert "disable_google_workspace_account" in actions
    assert "remove_github_member" in actions
    assert "remove_slack_member" in actions
