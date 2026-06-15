"""Unit tests for the grant_vpn_access operator tool (pure logic, no network/DB)."""
import pytest

from scripts.grant_vpn_access import (
    GrantError,
    _auth_mode,
    build_description,
    build_key_body,
    parse_args,
)


def test_build_key_body_secure_defaults():
    body = build_key_body(
        tag="tag:collaborator",
        expiry_days=90,
        reusable=False,
        ephemeral=False,
        description="VPN access for Lucas Nelson",
    )
    create = body["capabilities"]["devices"]["create"]
    assert create["reusable"] is False  # single-use: one key, one device
    assert create["ephemeral"] is False  # collaborator device persists
    assert create["preauthorized"] is True  # no manual admin approval
    assert create["tags"] == ["tag:collaborator"]  # access governed by ACL tag
    assert body["expirySeconds"] == 90 * 86400  # time-boxed
    assert body["description"] == "VPN access for Lucas Nelson"


def test_build_description_includes_name_and_email():
    desc = build_description("Lucas Nelson", "lucas@example.com", "ops")
    assert "Lucas Nelson" in desc
    assert "<lucas@example.com>" in desc
    assert "issued by ops" in desc


def test_build_description_without_email():
    desc = build_description("Lucas Nelson", None, "ops")
    assert "Lucas Nelson" in desc
    assert "<" not in desc


def test_auth_mode_prefers_oauth():
    creds = {"oauth_client_id": "id", "oauth_client_secret": "secret", "api_key": "k"}
    assert _auth_mode(creds) == "oauth"


def test_auth_mode_api_key():
    assert _auth_mode({"api_key": "tskey-api-xxx"}) == "api_key"
    assert _auth_mode({"token": "tskey-api-xxx"}) == "api_key"


def test_auth_mode_reusable_only_is_refused():
    # A single shared reusable key must not be used for per-person issuance.
    with pytest.raises(GrantError, match="per-person"):
        _auth_mode({"auth_key": "tskey-auth-shared"})


def test_auth_mode_no_creds_is_refused():
    with pytest.raises(GrantError):
        _auth_mode({})


def test_parse_args_defaults():
    args = parse_args(["Lucas Nelson"])
    assert args.name == "Lucas Nelson"
    assert args.tag == "tag:collaborator"
    assert args.expiry_days == 90
    assert args.reusable is False
    assert args.ephemeral is False


def test_parse_args_list_tailnet_needs_no_name():
    args = parse_args(["--list-tailnet"])
    assert args.list_tailnet is True
    assert args.name is None
