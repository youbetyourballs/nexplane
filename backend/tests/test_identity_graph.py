import pytest
import uuid
from app.models.identity_profile import IdentityProfile, IdentityAccount
from app.connectors.executors.identity.fan_out_registry import (
    FAN_OUT_ACTIONS,
    get_fan_out_action,
    is_fan_out_change_type,
)


def test_identity_profile_fields():
    profile = IdentityProfile(
        display_name="Alice Smith",
        primary_email="alice@example.com",
        correlation_method="email",
    )
    assert profile.primary_email == "alice@example.com"
    assert profile.correlation_method == "email"
    assert profile.id is None  # not persisted


def test_identity_account_fields():
    profile_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    account = IdentityAccount(
        identity_profile_id=profile_id,
        connector_id=connector_id,
        connector_type="active_directory",
        external_id="CN=alice,DC=corp,DC=local",
        username="alice",
        email="alice@corp.local",
        raw_attributes={"enabled": True},
        is_stale=False,
    )
    assert account.connector_type == "active_directory"
    assert account.raw_attributes == {"enabled": True}
    assert account.is_stale is False


def test_fan_out_registry_emergency_lockout():
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["active_directory"] == "disable_account"
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["okta"] == "suspend_user"
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["github"] == "suspend_org_member"


def test_get_fan_out_action_returns_none_for_unknown():
    assert get_fan_out_action("emergency_user_lockout", "unknown_connector") is None


def test_is_fan_out_change_type():
    assert is_fan_out_change_type("emergency_user_lockout") is True
    assert is_fan_out_change_type("identity_snapshot") is False
    assert is_fan_out_change_type("deploy_agent") is False
