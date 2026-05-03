import uuid
import pytest
from app.connectors.executors.onboard_user import build_plan, DEFINITION


def _connector(ctype):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "onboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_no_connectors():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [])
    # Should still include report step
    assert len(steps) == 1
    assert steps[0]["action"] == "generate_onboarding_report"


@pytest.mark.asyncio
async def test_ad_is_phase_1():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [_connector("active_directory"), _connector("okta")])
    ad_step = next(s for s in steps if "active_directory" in s["action"])
    okta_step = next(s for s in steps if "okta" in s["action"])
    assert ad_step["phase"] == 1
    assert okta_step["phase"] == 2


@pytest.mark.asyncio
async def test_github_slack_are_phase_3():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [_connector("github"), _connector("slack")])
    for s in steps:
        if s["action"] in ("add_github_member", "invite_slack_member"):
            assert s["phase"] == 3


@pytest.mark.asyncio
async def test_report_is_always_last():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(
        payload,
        [_connector("active_directory"), _connector("okta"), _connector("github")],
    )
    assert steps[-1]["action"] == "generate_onboarding_report"
