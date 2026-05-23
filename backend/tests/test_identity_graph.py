import pytest
import uuid
from app.models.identity_profile import IdentityProfile, IdentityAccount
from app.connectors.executors.identity.fan_out_registry import (
    FAN_OUT_ACTIONS,
    get_fan_out_action,
    is_fan_out_change_type,
)
from app.services.identity_sync_service import (
    _extract_idp_cross_refs,
    _correlate_by_email,
)
from app.connectors.executors.identity.get_account_state import build_pre_state_from_raw
from app.connectors.executors.identity.restore_account_state import (
    compute_restore_ops,
    RESTORABLE_CONNECTOR_TYPES,
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


def test_extract_idp_cross_refs_okta():
    raw = {
        "profile": {
            "login": "alice@corp.com",
            "email": "alice@corp.com",
            "samAccountName": "alice",
            "githubUsername": "alice-gh",
        }
    }
    refs = _extract_idp_cross_refs("okta", raw)
    assert refs.get("primary_email") == "alice@corp.com"


def test_extract_idp_cross_refs_entra_id():
    raw = {
        "userPrincipalName": "alice@corp.onmicrosoft.com",
        "mail": "alice@corp.com",
        "onPremisesSamAccountName": "alice",
    }
    refs = _extract_idp_cross_refs("entra_id", raw)
    assert refs.get("primary_email") == "alice@corp.com"


def test_correlate_by_email_returns_email_key():
    raw = {"mail": "bob@example.com", "uid": "bob"}
    result = _correlate_by_email("freeipa", raw)
    assert result == "bob@example.com"


def test_correlate_by_email_fallback_fields():
    raw = {"email": "carol@example.com"}
    assert _correlate_by_email("ldap", raw) == "carol@example.com"

    raw2 = {"userPrincipalName": "dan@example.com"}
    assert _correlate_by_email("active_directory", raw2) == "dan@example.com"


def test_build_pre_state_active_directory():
    raw = {
        "enabled": True,
        "locked": False,
        "group_memberships": ["Domain Users", "VPN"],
        "mfa_enforced": False,
    }
    state = build_pre_state_from_raw("active_directory", raw)
    assert state["enabled"] is True
    assert state["group_memberships"] == ["Domain Users", "VPN"]


def test_build_pre_state_okta():
    raw = {
        "status": "ACTIVE",
        "mfa_enrolled_factors": ["totp"],
        "app_assignments": ["slack", "github"],
    }
    state = build_pre_state_from_raw("okta", raw)
    assert state["status"] == "ACTIVE"
    assert "mfa_enrolled_factors" in state


def test_build_pre_state_unknown_connector():
    raw = {"foo": "bar"}
    state = build_pre_state_from_raw("unknown_system", raw)
    assert state == {}


def test_compute_restore_ops_disable_to_enabled():
    pre_state = {"enabled": True, "locked": False, "group_memberships": []}
    post_state = {"enabled": False, "locked": False, "group_memberships": []}
    ops = compute_restore_ops("active_directory", pre_state, post_state)
    assert any(op["action"] == "enable_account" for op in ops)


def test_restorable_connector_types_includes_common():
    assert "active_directory" in RESTORABLE_CONNECTOR_TYPES
    assert "okta" in RESTORABLE_CONNECTOR_TYPES
    assert "freeipa" in RESTORABLE_CONNECTOR_TYPES
