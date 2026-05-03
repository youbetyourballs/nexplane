import uuid
import pytest
from app.schemas.review_campaign import CampaignCreate, ReviewEntryOut, EntryDecisionSubmit


def test_campaign_create_valid():
    data = CampaignCreate(
        title="Q2 2026 SOC 1",
        campaign_type="manager_centric",
        scope={"connector_ids": None, "asset_tags": None, "user_groups": None, "include_inactive_users": False},
        reviewer_assignment_rule={"type": "manager_centric", "fallback_reviewer_id": str(uuid.uuid4())},
        evidence_options={"include_last_login": True, "include_days_inactive": True, "include_asset_sensitivity": True},
    )
    assert data.title == "Q2 2026 SOC 1"


def test_campaign_create_invalid_type():
    with pytest.raises(Exception):
        CampaignCreate(
            title="test",
            campaign_type="invalid_type",
            scope={},
            reviewer_assignment_rule={"type": "invalid_type", "fallback_reviewer_id": None},
            evidence_options={},
        )


def test_entry_decision_valid():
    d = EntryDecisionSubmit(decision="revoke", note="No longer needed")
    assert d.decision == "revoke"


def test_entry_decision_invalid():
    with pytest.raises(Exception):
        EntryDecisionSubmit(decision="maybe")
