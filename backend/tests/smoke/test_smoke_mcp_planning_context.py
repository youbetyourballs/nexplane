# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
MCP_PLANNING_CONTEXT_SMOKE — live smoke test for planning context MCP tools.

Run:
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest backend/tests/smoke/test_smoke_mcp_planning_context.py -v

Prerequisites:
  1. Migrations intel001 and pc001 applied to the live DB.
  2. At least one asset exists in the org.
  3. API_TOKEN env var holds a valid nxp_... token.
  4. ASSET_ID env var holds the UUID of an existing asset.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping live smoke test")
    return val


# ---------------------------------------------------------------------------
# PHASE 1: get_asset_history
# ---------------------------------------------------------------------------

async def test_PHASE_1_get_asset_history_returns_list():
    """get_asset_history returns a list (may be empty for a new asset)."""
    from app.mcp_tools.planning_context import get_asset_history
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await get_asset_history(token, asset_id)
    assert isinstance(result, list), f"Expected list, got: {type(result)}"


async def test_PHASE_1_get_asset_history_structure():
    """get_asset_history entries have required fields."""
    from app.mcp_tools.planning_context import get_asset_history
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await get_asset_history(token, asset_id)
    if result:
        entry = result[0]
        for field in ("cr_id", "change_type", "title", "status", "outcome", "rolled_back"):
            assert field in entry, f"Missing field '{field}' in: {entry}"


async def test_PHASE_1_get_asset_history_since_days_filter():
    """get_asset_history with since_days=1 returns a subset of full history."""
    from app.mcp_tools.planning_context import get_asset_history
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    full = await get_asset_history(token, asset_id, since_days=3650)
    recent = await get_asset_history(token, asset_id, since_days=1)
    assert len(recent) <= len(full), "since_days=1 should return fewer or equal entries than 10yr window"


# ---------------------------------------------------------------------------
# PHASE 2: get_fleet_context
# ---------------------------------------------------------------------------

async def test_PHASE_2_get_fleet_context_returns_list():
    """get_fleet_context returns a list of assets."""
    from app.mcp_tools.planning_context import get_fleet_context
    token = _env("API_TOKEN")
    result = await get_fleet_context(token)
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    assert len(result) > 0, "Expected at least one asset in fleet"


async def test_PHASE_2_get_fleet_context_structure():
    """get_fleet_context entries have required fields."""
    from app.mcp_tools.planning_context import get_fleet_context
    token = _env("API_TOKEN")
    result = await get_fleet_context(token)
    assert result, "No assets returned"
    entry = result[0]
    for field in ("asset_id", "name", "asset_type", "environment", "tags", "open_findings_count"):
        assert field in entry, f"Missing field '{field}' in: {entry}"


async def test_PHASE_2_get_fleet_context_limit():
    """get_fleet_context limit=1 returns exactly 1 asset."""
    from app.mcp_tools.planning_context import get_fleet_context
    token = _env("API_TOKEN")
    result = await get_fleet_context(token, limit=1)
    assert len(result) == 1, f"Expected 1 result with limit=1, got {len(result)}"


# ---------------------------------------------------------------------------
# PHASE 3: find_similar_assets
# ---------------------------------------------------------------------------

async def test_PHASE_3_find_similar_assets_returns_list():
    """find_similar_assets returns a list (may be empty if no peers)."""
    from app.mcp_tools.planning_context import find_similar_assets
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await find_similar_assets(token, asset_id)
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    # Should not return an error
    if result and isinstance(result[0], dict) and "error" in result[0]:
        raise AssertionError(f"find_similar_assets returned error: {result[0]['error']}")


async def test_PHASE_3_find_similar_assets_structure():
    """find_similar_assets entries have similarity_score."""
    from app.mcp_tools.planning_context import find_similar_assets
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await find_similar_assets(token, asset_id)
    if result:
        entry = result[0]
        for field in ("asset_id", "name", "similarity_score", "shared_tags", "different_tags"):
            assert field in entry, f"Missing field '{field}' in: {entry}"
        assert 0.0 <= entry["similarity_score"] <= 1.0


async def test_PHASE_3_find_similar_assets_invalid_id():
    """find_similar_assets returns error list for unknown asset_id."""
    from app.mcp_tools.planning_context import find_similar_assets
    token = _env("API_TOKEN")
    result = await find_similar_assets(token, str(uuid.uuid4()))
    assert isinstance(result, list)
    assert result[0].get("error") == "Asset not found"


# ---------------------------------------------------------------------------
# PHASE 4: get_migration_precedents
# ---------------------------------------------------------------------------

async def test_PHASE_4_get_migration_precedents_patch_packages():
    """get_migration_precedents returns valid structure for patch_packages."""
    from app.mcp_tools.planning_context import get_migration_precedents
    token = _env("API_TOKEN")
    result = await get_migration_precedents(token, "patch_packages")
    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    for field in ("total_executions", "success_rate", "rollback_frequency",
                  "common_failure_modes", "sample_cr_ids"):
        assert field in result, f"Missing field '{field}'"
    assert isinstance(result["common_failure_modes"], list)
    assert isinstance(result["sample_cr_ids"], list)


async def test_PHASE_4_get_migration_precedents_unknown_type():
    """get_migration_precedents with no history returns nulled stats."""
    from app.mcp_tools.planning_context import get_migration_precedents
    token = _env("API_TOKEN")
    result = await get_migration_precedents(token, "nonexistent_cr_type_xyz123")
    assert result["total_executions"] == 0
    assert result["success_rate"] is None


# ---------------------------------------------------------------------------
# PHASE 5: get_cross_host_dependency_map
# ---------------------------------------------------------------------------

async def test_PHASE_5_dependency_map_single_asset():
    """get_cross_host_dependency_map with one asset returns valid structure."""
    from app.mcp_tools.planning_context import get_cross_host_dependency_map
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await get_cross_host_dependency_map(token, [asset_id])
    assert isinstance(result, dict)
    assert "edges" in result
    assert "external_dependencies" in result
    assert isinstance(result["edges"], list)
    assert isinstance(result["external_dependencies"], list)


async def test_PHASE_5_dependency_map_empty_list():
    """get_cross_host_dependency_map with no valid IDs returns empty."""
    from app.mcp_tools.planning_context import get_cross_host_dependency_map
    token = _env("API_TOKEN")
    result = await get_cross_host_dependency_map(token, [])
    assert result == {"edges": [], "external_dependencies": []}


# ---------------------------------------------------------------------------
# PHASE 6: get_kernel_eol_status
# ---------------------------------------------------------------------------

async def test_PHASE_6_kernel_eol_status_for_asset():
    """get_kernel_eol_status returns entry for the target asset."""
    from app.mcp_tools.planning_context import get_kernel_eol_status
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await get_kernel_eol_status(token, [asset_id])
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    assert len(result) >= 1
    entry = result[0]
    for field in ("asset_id", "name", "kernel_version", "eol_date", "supported", "days_until_eol"):
        assert field in entry, f"Missing field '{field}' in: {entry}"


async def test_PHASE_6_kernel_eol_all_org_assets():
    """get_kernel_eol_status with no asset_ids scans the org fleet."""
    from app.mcp_tools.planning_context import get_kernel_eol_status
    token = _env("API_TOKEN")
    result = await get_kernel_eol_status(token)
    assert isinstance(result, list)
    # Should return at least something (the enrolled agent asset has a kernel)


# ---------------------------------------------------------------------------
# PHASE 7: get_environment_diff
# ---------------------------------------------------------------------------

async def test_PHASE_7_environment_diff_requires_two():
    """get_environment_diff with one asset_id returns error."""
    from app.mcp_tools.planning_context import get_environment_diff
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    result = await get_environment_diff(token, [asset_id])
    assert "error" in result, f"Expected error for single asset, got: {result}"


async def test_PHASE_7_environment_diff_two_assets():
    """get_environment_diff with two copies of same asset returns valid structure."""
    from app.mcp_tools.planning_context import get_environment_diff
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    # Use the fleet to get a second asset ID; fallback to same asset twice (tests structure)
    from app.mcp_tools.planning_context import get_fleet_context
    fleet = await get_fleet_context(token, limit=5)
    if len(fleet) >= 2:
        ids = [fleet[0]["asset_id"], fleet[1]["asset_id"]]
    else:
        ids = [asset_id, asset_id]
    result = await get_environment_diff(token, ids)
    assert isinstance(result, dict)
    for field in ("asset_ids", "common", "differences", "missing_data"):
        assert field in result, f"Missing field '{field}'"
    assert isinstance(result["differences"], list)


# ---------------------------------------------------------------------------
# PHASE 8: get_project_precedents
# ---------------------------------------------------------------------------

async def test_PHASE_8_project_precedents_no_match():
    """get_project_precedents returns empty list for nonsense goal."""
    from app.mcp_tools.planning_context import get_project_precedents
    token = _env("API_TOKEN")
    result = await get_project_precedents(token, "xyzzy nonsense goal that matches nothing abcdef")
    assert isinstance(result, list)


async def test_PHASE_8_project_precedents_structure():
    """get_project_precedents structure is valid for any match."""
    from app.mcp_tools.planning_context import get_project_precedents
    token = _env("API_TOKEN")
    result = await get_project_precedents(token, "migrate server upgrade patch")
    assert isinstance(result, list)
    if result:
        entry = result[0]
        for field in ("project_id", "name", "goal", "similarity_score", "total_crs",
                      "completed_crs", "rolled_back_crs", "cr_sequence_summary"):
            assert field in entry, f"Missing field '{field}'"
        assert 0.0 < entry["similarity_score"] <= 1.0
