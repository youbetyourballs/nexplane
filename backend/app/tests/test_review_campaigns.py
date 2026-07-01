# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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


from unittest.mock import MagicMock
from app.services.review_collector import extract_entries_from_asset, enrich_entry, resolve_reviewer


def test_extract_entries_okta_asset():
    connector = MagicMock()
    connector.connector_type.value = "okta"
    asset = MagicMock()
    asset.name = "jane.doe@acme.com"
    asset.connector = connector
    asset.connector_id = uuid.uuid4()
    asset.asset_metadata = {
        "email": "jane.doe@acme.com",
        "display_name": "Jane Doe",
        "status": "ACTIVE",
        "groups": ["Engineering", "All Users"],
        "app_assignments": [{"app_name": "GitHub Enterprise", "role": "member"}],
        "is_admin": False,
    }
    entries = extract_entries_from_asset(asset)
    assert len(entries) == 3  # 2 groups + 1 app
    resource_names = [e["resource_name"] for e in entries]
    assert "Engineering" in resource_names
    assert "GitHub Enterprise" in resource_names


def test_extract_entries_active_directory():
    connector = MagicMock()
    connector.connector_type.value = "active_directory"
    asset = MagicMock()
    asset.name = "john.smith@acme.com"
    asset.connector = connector
    asset.connector_id = uuid.uuid4()
    asset.asset_metadata = {
        "email": "john.smith@acme.com",
        "groups": ["Domain Admins", "IT Staff"],
        "status": "enabled",
    }
    entries = extract_entries_from_asset(asset)
    assert len(entries) == 2
    privileged = [e for e in entries if e["is_privileged"]]
    assert len(privileged) == 1  # Domain Admins is privileged


def test_resolve_reviewer_security_team():
    rule = {"type": "security_team", "fallback_reviewer_id": "abc123"}
    result = resolve_reviewer({}, rule, manager_email=None, owner_email=None)
    assert result == ("abc123", False)


def test_resolve_reviewer_manager_found():
    rule = {"type": "manager_centric", "fallback_reviewer_id": "fallback"}
    result = resolve_reviewer({}, rule, manager_email="manager@acme.com", owner_email=None)
    assert result == ("manager@acme.com", False)


def test_resolve_reviewer_fallback_when_no_manager():
    rule = {"type": "manager_centric", "fallback_reviewer_id": "fallback-uuid"}
    result = resolve_reviewer({}, rule, manager_email=None, owner_email=None)
    assert result == ("fallback-uuid", True)


def test_enrich_entry_flags_inactive():
    identity_asset = MagicMock()
    identity_asset.asset_metadata = {"last_login_at": "2025-01-01T00:00:00Z"}
    evidence = enrich_entry({}, identity_asset, None, {"include_last_login": True, "include_days_inactive": True, "include_asset_sensitivity": False})
    assert evidence["flagged_inactive"] is True
    assert evidence["days_inactive"] > 90


from app.services.review_approver import connector_type_to_change_type


def test_connector_change_type_okta():
    assert connector_type_to_change_type("okta") == "rotate_service_account"


def test_connector_change_type_github():
    assert connector_type_to_change_type("github") == "offboard_user"


def test_connector_change_type_fallback():
    assert connector_type_to_change_type("unknown_connector") == "remote_command"


import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from app.main import app as fastapi_app


@pytest.mark.anyio
async def test_create_campaign_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        resp = await client.post("/review-campaigns", json={
            "title": "Test",
            "campaign_type": "security_team",
            "scope": {},
            "reviewer_assignment_rule": {"type": "security_team"},
            "evidence_options": {},
        })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_campaigns_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        resp = await client.get("/review-campaigns")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_cancel_nonexistent_requires_auth():
    import uuid
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        resp = await client.post(f"/review-campaigns/{uuid.uuid4()}/cancel")
    assert resp.status_code == 401
