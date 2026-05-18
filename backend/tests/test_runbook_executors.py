"""Unit tests for new runbook change type executors."""
from __future__ import annotations
import pytest
from app.models.change_request import ChangeType


def test_new_change_types_in_enum():
    expected = [
        "create_ad_account",
        "assign_okta_groups",
        "add_github_org_member",
        "send_welcome_email",
        "preserve_cloudtrail_logs",
        "force_password_reset",
        "close_incident_ticket",
        "check_fleet_health",
        "check_compliance",
    ]
    for name in expected:
        assert name in ChangeType.__members__, f"ChangeType.{name} is missing"


import asyncio


class _MockConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {}


# --- AD create_user ---

@pytest.mark.asyncio
async def test_ad_create_user_no_creds_returns_simulated():
    from app.connectors.executors.active_directory import create_user
    result = await create_user.execute(
        {"username": "jdoe", "first_name": "John", "last_name": "Doe",
         "ou": "OU=Users,DC=corp,DC=local", "temp_password": "Temp1234!"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "create_ad_account"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_ad_create_user_rollback_no_creds():
    from app.connectors.executors.active_directory import create_user
    result = await create_user.rollback(
        {"username": "jdoe"},
        {"dn": "CN=jdoe,OU=Users,DC=corp,DC=local", "created": True},
        _MockConnector(),
    )
    assert result["rolled_back"] is True


# --- Okta assign_groups ---

@pytest.mark.asyncio
async def test_okta_assign_groups_no_creds_returns_simulated():
    from app.connectors.executors.okta import assign_groups
    result = await assign_groups.execute(
        {"user_id": "00u1abc", "group_ids": ["00g1", "00g2"]},
        [],
        _MockConnector(),
    )
    assert result["action"] == "assign_okta_groups"
    assert result["simulated"] is True
    assert result["assigned_groups"] == ["00g1", "00g2"]


@pytest.mark.asyncio
async def test_okta_assign_groups_rollback_no_creds():
    from app.connectors.executors.okta import assign_groups
    result = await assign_groups.rollback(
        {"user_id": "00u1abc"},
        {"user_id": "00u1abc", "assigned_groups": ["00g1", "00g2"]},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["removed_groups"] == ["00g1", "00g2"]


# --- Okta force_password_reset ---

@pytest.mark.asyncio
async def test_okta_force_password_reset_no_creds_returns_simulated():
    from app.connectors.executors.okta import force_password_reset
    result = await force_password_reset.execute(
        {"user_id": "00u1abc"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "force_password_reset"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_okta_force_password_reset_rollback_is_noop():
    from app.connectors.executors.okta import force_password_reset
    result = await force_password_reset.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False
    assert "reason" in result
