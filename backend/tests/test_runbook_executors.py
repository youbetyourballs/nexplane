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
