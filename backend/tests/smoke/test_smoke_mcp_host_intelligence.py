# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
MCP_HOST_INTELLIGENCE_SMOKE — live smoke test for host intelligence MCP tools.

Run:
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest backend/tests/smoke/test_smoke_mcp_host_intelligence.py -v

Prerequisites:
  1. Migration intel001 applied to the live DB.
  2. At least one asset has a live Nexplane agent registered.
  3. API_TOKEN env var holds a valid nxp_... token.
  4. ASSET_ID env var holds the UUID of that asset.
"""
from __future__ import annotations

import os
import uuid

import pytest

# All tests in this module share one event loop so the asyncpg connection pool
# (bound to the first loop) stays valid across test functions.
pytestmark = pytest.mark.asyncio(loop_scope="session")


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping live smoke test")
    return val


async def _invoke(tool_fn, token: str, asset_id: str):
    result = await tool_fn(token, asset_id)
    if isinstance(result, dict) and "error" in result:
        raise AssertionError(f"{tool_fn.__name__} returned error: {result['error']}")
    if isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0]:
        raise AssertionError(f"{tool_fn.__name__} returned error list: {result[0]['error']}")
    return result


async def test_PHASE_1_get_kernel_info():
    from app.mcp_tools.host_intelligence import get_kernel_info
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_kernel_info, token, asset_id)
    assert "version" in result, f"Missing 'version' key: {result}"
    assert "arch" in result, f"Missing 'arch' key: {result}"


async def test_PHASE_1_get_running_processes():
    from app.mcp_tools.host_intelligence import get_running_processes
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_running_processes, token, asset_id)
    assert isinstance(result, list), "Expected list"
    assert len(result) > 0, "No processes returned"
    assert "pid" in result[0]
    assert "name" in result[0]


async def test_PHASE_1_get_cron_jobs():
    from app.mcp_tools.host_intelligence import get_cron_jobs
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_cron_jobs, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "schedule" in result[0]


async def test_PHASE_1_get_local_users():
    from app.mcp_tools.host_intelligence import get_local_users
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_local_users, token, asset_id)
    assert isinstance(result, list)
    assert len(result) > 0, "No users returned"
    assert "username" in result[0]


async def test_PHASE_1_get_installed_packages():
    from app.mcp_tools.host_intelligence import get_installed_packages
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_installed_packages, token, asset_id)
    assert isinstance(result, list)
    assert len(result) > 0, "No packages returned"
    assert "name" in result[0]


async def test_PHASE_1_get_running_services():
    from app.mcp_tools.host_intelligence import get_running_services
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_running_services, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "name" in result[0]


async def test_PHASE_1_get_open_ports():
    from app.mcp_tools.host_intelligence import get_open_ports
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_open_ports, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "port" in result[0]


async def test_PHASE_1_get_security_posture():
    from app.mcp_tools.host_intelligence import get_security_posture
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_security_posture, token, asset_id)
    assert isinstance(result, dict)
    assert "selinux_mode" in result


async def test_PHASE_1_get_seccomp_policy():
    from app.mcp_tools.host_intelligence import get_seccomp_policy
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_seccomp_policy, token, asset_id)
    assert isinstance(result, dict)
    assert "active_profiles" in result


async def test_PHASE_1_get_apparmor_profiles():
    from app.mcp_tools.host_intelligence import get_apparmor_profiles
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_apparmor_profiles, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_selinux_policy():
    from app.mcp_tools.host_intelligence import get_selinux_policy
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_selinux_policy, token, asset_id)
    assert isinstance(result, dict)
    assert "mode" in result


async def test_PHASE_1_get_sudoers():
    from app.mcp_tools.host_intelligence import get_sudoers
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_sudoers, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_authorized_keys():
    from app.mcp_tools.host_intelligence import get_authorized_keys
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_authorized_keys, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_ssl_certs():
    from app.mcp_tools.host_intelligence import get_ssl_certs
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_ssl_certs, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_patch_status():
    from app.mcp_tools.host_intelligence import get_patch_status
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await _invoke(get_patch_status, token, asset_id)
    assert isinstance(result, dict)
    assert "pending_patches_count" in result
    assert "critical_pending" in result


async def test_PHASE_2_cache_hit_kernel():
    """Second call within 300 s must return without dispatching a new agent job."""
    from app.mcp_tools.host_intelligence import get_kernel_info
    from app.database import AsyncSessionLocal
    from app.models.mcp_intelligence_cache import McpIntelligenceCache
    from sqlalchemy import select, func

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    # First call — populates cache
    await _invoke(get_kernel_info, token, asset_id)

    # Count cache entries before second call
    async with AsyncSessionLocal() as db:
        before = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.tool_name == "get_kernel_info"
            )
        )
        count_before = before.scalar()

    # Second call — should hit cache
    result2 = await _invoke(get_kernel_info, token, asset_id)

    async with AsyncSessionLocal() as db:
        after = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.tool_name == "get_kernel_info"
            )
        )
        count_after = after.scalar()

    assert count_after == count_before, (
        f"Cache grew from {count_before} to {count_after} — second call re-dispatched"
    )
    assert "version" in result2


async def test_PHASE_3_get_host_full_context():
    from app.mcp_tools.host_intelligence import get_host_full_context

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    result = await get_host_full_context(token, asset_id)

    assert "error" not in result, f"full_context returned error: {result.get('error')}"

    expected_keys = {
        "asset_id", "kernel", "processes", "cron_jobs", "local_users",
        "installed_packages", "running_services", "open_ports", "security_posture",
        "seccomp_policy", "apparmor_profiles", "selinux_policy", "sudoers",
        "authorized_keys", "ssl_certs", "patch_status",
    }
    missing = expected_keys - set(result.keys())
    assert not missing, f"Missing keys in full_context: {missing}"


async def test_PHASE_4_cache_invalidate_forces_redispatch():
    from app.mcp_tools.host_intelligence import get_kernel_info
    from app.services.host_intelligence_service import invalidate_cache
    from app.database import AsyncSessionLocal
    from app.models.mcp_intelligence_cache import McpIntelligenceCache
    from app.models.asset import Asset
    from sqlalchemy import select, func
    import uuid as _uuid

    token = _env("API_TOKEN")
    asset_id_str = _env("ASSET_ID")
    asset_uuid = _uuid.UUID(asset_id_str)

    # Ensure at least one cache entry exists
    await _invoke(get_kernel_info, token, asset_id_str)

    # Find the org_id for the asset
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_uuid)
        assert asset, "ASSET_ID not found in DB"
        org_id = asset.organization_id

    # Invalidate
    async with AsyncSessionLocal() as db:
        await invalidate_cache(db, org_id, asset_id_str)

    # Verify cache is empty for this asset
    async with AsyncSessionLocal() as db:
        count_result = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.asset_id == asset_uuid,
            )
        )
        count = count_result.scalar()
    assert count == 0, f"Expected 0 cache entries after invalidate, got {count}"

    # Next call must re-populate cache
    result = await _invoke(get_kernel_info, token, asset_id_str)
    assert "version" in result

    async with AsyncSessionLocal() as db:
        count_result = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.asset_id == asset_uuid,
                McpIntelligenceCache.tool_name == "get_kernel_info",
            )
        )
        count_after = count_result.scalar()
    assert count_after == 1, f"Expected 1 cache entry after re-dispatch, got {count_after}"
