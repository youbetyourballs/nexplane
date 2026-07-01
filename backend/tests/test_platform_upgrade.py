# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for platform upgrade executor — phases 1–4 and sentinel logic."""
import json
import os
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


# ── Sentinel helpers ────────────────────────────────────────────────────────

def test_write_sentinel_creates_file(tmp_path):
    from app.connectors.executors.platform.upgrade import write_sentinel
    sentinel = tmp_path / "sentinel.json"
    write_sentinel({"state": "preflight_complete", "cr_id": "abc"}, path=str(sentinel))
    data = json.loads(sentinel.read_text())
    assert data["state"] == "preflight_complete"
    assert data["cr_id"] == "abc"


def test_read_sentinel_returns_none_when_missing(tmp_path):
    from app.connectors.executors.platform.upgrade import read_sentinel
    result = read_sentinel(path=str(tmp_path / "nonexistent.json"))
    assert result is None


def test_read_sentinel_returns_dict_when_present(tmp_path):
    from app.connectors.executors.platform.upgrade import read_sentinel
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text('{"state": "pull_complete"}')
    result = read_sentinel(path=str(sentinel))
    assert result == {"state": "pull_complete"}


# ── Phase 1: preflight ───────────────────────────────────────────────────────

def test_check_version_newer_passes():
    from app.connectors.executors.platform.upgrade import check_version_newer
    check_version_newer(current="1.3.1", target="1.4.2")  # no exception


def test_check_version_newer_fails_same():
    from app.connectors.executors.platform.upgrade import check_version_newer
    with pytest.raises(ValueError, match="not newer"):
        check_version_newer(current="1.4.2", target="1.4.2")


def test_check_version_newer_fails_downgrade():
    from app.connectors.executors.platform.upgrade import check_version_newer
    with pytest.raises(ValueError, match="not newer"):
        check_version_newer(current="1.5.0", target="1.4.2")


def test_check_min_compatible_passes():
    from app.connectors.executors.platform.upgrade import check_min_compatible
    check_min_compatible(current="1.3.0", min_version="1.2.0")


def test_check_min_compatible_fails():
    from app.connectors.executors.platform.upgrade import check_min_compatible
    with pytest.raises(ValueError, match="below minimum"):
        check_min_compatible(current="1.1.0", min_version="1.2.0")


# ── Phase 3: sha256 verification ────────────────────────────────────────────

def test_verify_image_sha_passes():
    from app.connectors.executors.platform.upgrade import verify_image_sha
    verify_image_sha(
        pulled_sha="sha256:abc123",
        expected_sha="sha256:abc123",
    )


def test_verify_image_sha_fails():
    from app.connectors.executors.platform.upgrade import verify_image_sha
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_image_sha(pulled_sha="sha256:bad", expected_sha="sha256:abc123")


# ── Execute wiring ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_returns_mock_when_no_creds():
    from app.connectors.executors.platform.upgrade import execute
    connector = MagicMock()
    connector.credentials = {}
    result = await execute(
        {"target_version": "1.4.2", "image_sha256": "sha256:abc", "cr_id": "test-cr"},
        [],
        connector,
    )
    assert result.get("mock") is True
    assert result["action"] == "platform_upgrade"
