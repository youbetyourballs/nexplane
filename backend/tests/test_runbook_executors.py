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


# --- GitHub add_org_member ---

@pytest.mark.asyncio
async def test_github_add_org_member_no_creds_returns_simulated():
    from app.connectors.executors.github import add_org_member
    result = await add_org_member.execute(
        {"username": "octocat", "org": "nexplane-org", "role": "member"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "add_org_member"
    assert result["simulated"] is True
    assert result["username"] == "octocat"


@pytest.mark.asyncio
async def test_github_add_org_member_rollback_removes():
    from app.connectors.executors.github import add_org_member
    result = await add_org_member.rollback(
        {"username": "octocat", "org": "nexplane-org"},
        {"username": "octocat", "org": "nexplane-org", "added": True},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["simulated"] is True


# --- SMTP send_welcome_email ---

@pytest.mark.asyncio
async def test_smtp_send_welcome_email_no_creds_returns_simulated():
    from app.connectors.executors.smtp import send_welcome_email
    result = await send_welcome_email.execute(
        {
            "to_address": "jdoe@example.com",
            "recipient_name": "John Doe",
            "temp_password": "Temp1234!",
            "login_url": "https://login.example.com",
        },
        [],
        _MockConnector(),
    )
    assert result["action"] == "send_welcome_email"
    assert result["simulated"] is True
    assert result["to_address"] == "jdoe@example.com"


@pytest.mark.asyncio
async def test_smtp_send_welcome_email_rollback_no_creds_reports_no_account():
    from app.connectors.executors.smtp import send_welcome_email
    result = await send_welcome_email.rollback(
        {},
        {"to_address": "jdoe@example.com"},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["account_removed"] is False


# --- AWS preserve_cloudtrail_logs ---

@pytest.mark.asyncio
async def test_aws_preserve_cloudtrail_logs_no_creds_returns_simulated():
    from app.connectors.executors.aws import preserve_cloudtrail_logs
    result = await preserve_cloudtrail_logs.execute(
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/", "region": "us-east-1"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "preserve_cloudtrail_logs"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_aws_preserve_cloudtrail_logs_rollback_no_creds():
    from app.connectors.executors.aws import preserve_cloudtrail_logs
    result = await preserve_cloudtrail_logs.rollback(
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/"},
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/", "objects_locked": 5},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["simulated"] is True


# --- ServiceNow close_incident_ticket ---

@pytest.mark.asyncio
async def test_servicenow_close_incident_no_creds_returns_mock():
    from app.connectors.executors.servicenow import close_incident
    result = await close_incident.execute(
        {"sys_id": "abc123"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "close_incident"
    assert result["state"] == "7"


@pytest.mark.asyncio
async def test_servicenow_close_incident_rollback_reopens_no_creds():
    from app.connectors.executors.servicenow import close_incident
    result = await close_incident.rollback(
        {"sys_id": "abc123"},
        {"sys_id": "abc123", "state": "7"},
        _MockConnector(),
    )
    assert result["rolled_back"] is True


# --- check_fleet_health ---

@pytest.mark.asyncio
async def test_check_fleet_health_no_creds_returns_simulated():
    from app.connectors.executors.nexplane_agent import check_fleet_health
    result = await check_fleet_health.execute(
        {"asset_ids": ["asset-1", "asset-2"], "environment": "production"},
        ["asset-1", "asset-2"],
        _MockConnector(),
    )
    assert result["action"] == "check_fleet_health"
    assert "healthy" in result
    assert "degraded" in result
    assert "unreachable" in result


@pytest.mark.asyncio
async def test_check_fleet_health_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import check_fleet_health
    result = await check_fleet_health.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False


# --- check_compliance ---

@pytest.mark.asyncio
async def test_check_compliance_no_creds_returns_simulated():
    from app.connectors.executors.nexplane_agent import check_compliance
    result = await check_compliance.execute(
        {"asset_ids": ["asset-1"], "framework": "cis"},
        ["asset-1"],
        _MockConnector(),
    )
    assert result["action"] == "check_compliance"
    assert "compliant" in result
    assert "non_compliant" in result
    assert "unknown" in result


@pytest.mark.asyncio
async def test_check_compliance_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import check_compliance
    result = await check_compliance.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False
