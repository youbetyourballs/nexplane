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
