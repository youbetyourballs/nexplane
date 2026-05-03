import uuid
from app.schemas.access_review import (
    AccessReviewCreate,
    AccessReviewOut,
    AccessReviewDecisionsSubmit,
    AccessReviewDecisionItem,
)
from app.schemas.change_request import OffboardUserPayload, OnboardUserPayload


def test_access_review_create_requires_title_and_scope():
    r = AccessReviewCreate(title="Q2 Review", scope={"connector_ids": None, "groups": None, "user_emails": None})
    assert r.title == "Q2 Review"


def test_access_review_decisions_submit():
    entry_id = str(uuid.uuid4())
    r = AccessReviewDecisionsSubmit(
        decisions={entry_id: AccessReviewDecisionItem(decision="revoke", note="No longer on project")}
    )
    assert r.decisions[entry_id].decision == "revoke"


def test_access_review_decision_invalid():
    import pytest
    with pytest.raises(Exception):
        AccessReviewDecisionItem(decision="maybe")


def test_offboard_payload_defaults():
    p = OffboardUserPayload(target_email="alice@corp.com", reason="termination")
    assert p.isolate_endpoints is False
    assert p.notify_manager is True
    assert p.manager_email is None


def test_onboard_payload():
    p = OnboardUserPayload(
        target_email="new@corp.com",
        display_name="New User",
        department="Engineering",
        manager_email="mgr@corp.com",
    )
    assert p.ad_groups == []
    assert p.github_teams == []
