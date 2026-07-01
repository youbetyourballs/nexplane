# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from pydantic import ValidationError


def _make_settings(**kwargs):
    from app.config import Settings
    return Settings(**kwargs)


def test_development_allows_default_secret_key():
    s = _make_settings(ENVIRONMENT="development")
    assert s.SECRET_KEY == "dev-secret-key-change-in-production-32chars"


def test_development_allows_default_webhook_secret():
    s = _make_settings(ENVIRONMENT="development")
    assert s.WEBHOOK_SECRET == "changeme"


def test_production_rejects_default_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret-ok",
        )


def test_production_rejects_default_webhook_secret():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="a-real-production-secret-key-here-32chars!!",
            WEBHOOK_SECRET="changeme",
        )


def test_production_rejects_short_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="tooshort",
            WEBHOOK_SECRET="prod-safe-secret-ok",
        )


def test_production_accepts_safe_secrets():
    s = _make_settings(
        ENVIRONMENT="production",
        SECRET_KEY="a-real-production-secret-key-here-32chars!!",
        WEBHOOK_SECRET="prod-safe-webhook-secret-value",
    )
    assert s.ENVIRONMENT == "production"


def test_staging_rejects_default_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="staging",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret-ok",
        )


def test_production_rejects_short_webhook_secret():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="a-real-production-secret-key-here-32chars!!",
            WEBHOOK_SECRET="tooshort",
        )


def test_error_message_does_not_reveal_secret_value():
    try:
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret",
        )
        assert False, "should have raised"
    except ValueError as exc:
        assert "dev-secret-key-change-in-production-32chars" not in str(exc)
