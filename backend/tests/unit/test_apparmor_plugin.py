# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for AppArmor synthesizer plugin."""
import pytest
from app.services.security_policy.plugins.apparmor import _synthesize, _delta_extract


def test_synthesize_empty_observations():
    profile = _synthesize({})
    assert "profile_text" in profile
    assert profile["mode"] == "complain"
    assert "{service_name}" in profile["profile_name"]


def test_synthesize_file_and_capability():
    obs = {
        "asset-1": [
            {"operation": "file_read",  "resource": "/etc/nginx/nginx.conf"},
            {"operation": "file_write", "resource": "/var/log/nginx/access.log"},
            {"operation": "capability", "resource": "net_bind_service"},
            {"operation": "network",    "resource": "tcp"},
        ]
    }
    profile = _synthesize(obs)
    text = profile["profile_text"]
    assert "capability net_bind_service," in text
    assert "network tcp," in text
    assert "/etc/nginx/**" in text   # glob applied
    assert "/var/log/nginx/**" in text


def test_delta_extract_roundtrip():
    obs = {"a": [{"operation": "capability", "resource": "net_raw"}]}
    profile = _synthesize(obs)
    rules = _delta_extract(profile)
    assert "capability net_raw" in rules


def test_delta_empty_profile():
    assert _delta_extract({}) == set()
