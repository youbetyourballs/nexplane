# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Unit tests for host_intelligence MCP tools.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.host_intelligence_service import (
    _get_cached,
    _store,
    invalidate_cache,
    run_intelligence_tool,
)


def _make_user(org_id=None):
    from types import SimpleNamespace
    u = SimpleNamespace()
    u.organization_id = org_id or uuid.uuid4()
    return u


@pytest.mark.asyncio
async def test_cache_hit_skips_dispatch():
    """run_intelligence_tool returns cached result without calling dispatch."""
    org_id = uuid.uuid4()
    asset_id = str(uuid.uuid4())
    user = _make_user(org_id)
    cached_data = {"kernel": {"version": "5.15.0", "arch": "x86_64"}}

    db = AsyncMock()

    with patch(
        "app.services.host_intelligence_service._get_cached",
        new=AsyncMock(return_value=cached_data),
    ) as mock_cache, patch(
        "app.services.host_intelligence_service._dispatch",
        new=AsyncMock(side_effect=AssertionError("should not dispatch")),
    ):
        result = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_kernel_info", command="deep_discover",
            params={}, timeout=30,
        )

    assert result == cached_data
    mock_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_miss_dispatches_and_stores():
    """run_intelligence_tool dispatches on cache miss and stores the result."""
    org_id = uuid.uuid4()
    asset_id = str(uuid.uuid4())
    user = _make_user(org_id)
    dispatch_data = {"processes": [{"pid": 1, "name": "systemd"}]}

    db = AsyncMock()

    with patch(
        "app.services.host_intelligence_service._get_cached",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.host_intelligence_service._dispatch",
        new=AsyncMock(return_value=dispatch_data),
    ) as mock_dispatch, patch(
        "app.services.host_intelligence_service._store",
        new=AsyncMock(),
    ) as mock_store:
        result = await run_intelligence_tool(
            db=db, user=user, asset_id=asset_id,
            tool_name="get_running_processes", command="deep_discover",
            params={}, timeout=30,
        )

    assert result == dispatch_data
    mock_dispatch.assert_awaited_once_with("deep_discover", {}, asset_id, 30)
    mock_store.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_cache_deletes_entries():
    """invalidate_cache executes a DELETE and commits."""
    org_id = uuid.uuid4()
    asset_id = str(uuid.uuid4())

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    await invalidate_cache(db, org_id, asset_id)

    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_kernel_info_unknown_asset_returns_error():
    """get_kernel_info returns {"error": ...} if the asset is not in the user's org."""
    from app.mcp_tools.host_intelligence import get_kernel_info

    fake_token = "nxp_test"
    asset_id = str(uuid.uuid4())
    user = _make_user()

    async def _fake_auth(token):
        db_cm = AsyncMock()
        db_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
        db_cm.__aexit__ = AsyncMock(return_value=None)
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=AsyncMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        return user, db, db_cm

    with patch("app.mcp_tools.host_intelligence._auth", new=_fake_auth):
        result = await get_kernel_info(fake_token, asset_id)

    assert "error" in result


@pytest.mark.asyncio
async def test_get_host_full_context_merges_all_15_tools():
    """get_host_full_context returns a dict with all 15 expected keys."""
    from app.mcp_tools.host_intelligence import get_host_full_context

    asset_id = str(uuid.uuid4())
    fake_token = "nxp_test"
    user = _make_user()

    async def _fake_auth(token):
        db_cm = AsyncMock()
        db_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
        db_cm.__aexit__ = AsyncMock(return_value=None)
        db = AsyncMock()
        asset_mock = MagicMock()
        db.execute = AsyncMock(
            return_value=AsyncMock(scalar_one_or_none=MagicMock(return_value=asset_mock))
        )
        return user, db, db_cm

    sentinel = {"ok": True}
    tool_names = [
        "get_kernel_info", "get_running_processes", "get_cron_jobs",
        "get_local_users", "get_installed_packages", "get_running_services",
        "get_open_ports", "get_security_posture", "get_seccomp_policy",
        "get_apparmor_profiles", "get_selinux_policy", "get_sudoers",
        "get_authorized_keys", "get_ssl_certs", "get_patch_status",
    ]

    patches = {
        name: patch(
            f"app.mcp_tools.host_intelligence.{name}",
            new=AsyncMock(return_value=sentinel),
        )
        for name in tool_names
    }

    with patch("app.mcp_tools.host_intelligence._auth", new=_fake_auth):
        with patch("app.mcp_tools.host_intelligence._assert_asset_owned", new=AsyncMock()):
            for p in patches.values():
                p.start()
            try:
                result = await get_host_full_context(fake_token, asset_id)
            finally:
                for p in patches.values():
                    p.stop()

    expected_keys = {
        "asset_id", "kernel", "processes", "cron_jobs", "local_users",
        "installed_packages", "running_services", "open_ports", "security_posture",
        "seccomp_policy", "apparmor_profiles", "selinux_policy", "sudoers",
        "authorized_keys", "ssl_certs", "patch_status",
    }
    assert expected_keys == set(result.keys())
    assert result["kernel"] == sentinel
