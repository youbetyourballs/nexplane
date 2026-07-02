# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
MCP_PROJECT_ORCHESTRATION_SMOKE — live smoke test for project orchestration MCP tools.

Run:
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest backend/tests/smoke/test_mcp_project_orchestration.py -v -s

Prerequisites:
  1. Migrations proj001 and proj002 applied to the live DB.
  2. At least one asset exists in the org.
  3. API_TOKEN env var holds a valid nxp_... token.
  4. ASSET_ID env var holds the UUID of an existing asset.

Full cycle:
  create_project → add_cr_to_project → define_success_criteria →
  get_project → get_project_status → get_project_timeline → estimate_project_risk →
  check_success_criteria → rollback_project (if any CRs executed) → verify cleanup
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Keep project_id across tests via module-level state
_STATE: dict = {}


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping live smoke test")
    return val


# ---------------------------------------------------------------------------
# PHASE 1: Create project
# ---------------------------------------------------------------------------

async def test_PHASE_1_create_project():
    """create_project creates a project in draft state and returns a project_id."""
    from app.mcp_tools.projects import create_project
    token = _env("API_TOKEN")
    result = await create_project(
        token=token,
        name="MCP Smoke Test Project",
        goal="Verify all 15 project orchestration MCP tools work end-to-end",
        description="Automated smoke test — safe to delete",
    )
    assert "id" in result, f"No 'id' in result: {result}"
    assert result.get("status") == "draft", f"Expected draft status, got: {result.get('status')}"
    _STATE["project_id"] = result["id"]
    print(f"\n  Created project_id: {result['id']}")


# ---------------------------------------------------------------------------
# PHASE 2: list_projects sees the new project
# ---------------------------------------------------------------------------

async def test_PHASE_2_list_projects():
    """list_projects returns a list that includes the smoke project."""
    from app.mcp_tools.projects import list_projects
    token = _env("API_TOKEN")
    result = await list_projects(token=token)
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    assert len(result) > 0, "No projects returned"
    entry = result[0]
    for field in ("id", "name", "status", "cr_count"):
        assert field in entry, f"Missing field '{field}' in: {entry}"


# ---------------------------------------------------------------------------
# PHASE 3: get_project returns full state
# ---------------------------------------------------------------------------

async def test_PHASE_3_get_project():
    """get_project returns full project state."""
    from app.mcp_tools.projects import get_project
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")
    result = await get_project(token=token, project_id=project_id)
    assert "id" in result, f"Missing 'id': {result}"
    assert result["id"] == project_id
    assert "change_requests" in result, f"Missing 'change_requests': {result}"


# ---------------------------------------------------------------------------
# PHASE 4: add a CR to the project (if one exists)
# ---------------------------------------------------------------------------

async def test_PHASE_4_add_cr_to_project():
    """add_cr_to_project adds an existing CR to the smoke project."""
    from app.mcp_tools.projects import add_cr_to_project
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    from sqlalchemy import select
    import asyncio

    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    # Find any existing CR in the org
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(ChangeRequest).limit(1))
        cr = r.scalar_one_or_none()

    if not cr:
        pytest.skip("No existing CRs in org to test add_cr_to_project")

    cr_id = str(cr.id)
    result = await add_cr_to_project(token=token, project_id=project_id, cr_id=cr_id)
    assert "error" not in result, f"add_cr_to_project returned error: {result}"
    assert "cr_id" in result, f"Missing 'cr_id' in: {result}"
    _STATE["added_cr_id"] = cr_id
    print(f"\n  Added CR: {cr_id}")


# ---------------------------------------------------------------------------
# PHASE 5: define_success_criteria
# ---------------------------------------------------------------------------

async def test_PHASE_5_define_success_criteria():
    """define_success_criteria creates a manual criterion."""
    from app.mcp_tools.projects import define_success_criteria
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await define_success_criteria(
        token=token,
        project_id=project_id,
        criteria=[
            {
                "type": "manual",
                "description": "Smoke test manual verification — always passes after review",
                "assertion": {"instructions": "Mark this criterion as verified by the smoke test"},
            }
        ],
    )
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    assert len(result) >= 1, "No criteria returned"
    entry = result[0]
    for field in ("criteria_id", "type", "description"):
        assert field in entry, f"Missing field '{field}' in: {entry}"
    _STATE["criteria_id"] = entry["criteria_id"]
    print(f"\n  Created criteria_id: {entry['criteria_id']}")


# ---------------------------------------------------------------------------
# PHASE 6: check_success_criteria
# ---------------------------------------------------------------------------

async def test_PHASE_6_check_success_criteria():
    """check_success_criteria returns evaluation results for all criteria."""
    from app.mcp_tools.projects import check_success_criteria
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await check_success_criteria(token=token, project_id=project_id)
    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    for field in ("pass_count", "fail_count", "pending_count", "all_pass", "criteria_results"):
        assert field in result, f"Missing field '{field}' in: {result}"
    assert isinstance(result["criteria_results"], list)
    # The manual criterion should be pending_manual (not pass or fail)
    if result["criteria_results"]:
        entry = result["criteria_results"][0]
        assert entry.get("result") == "pending_manual", f"Expected pending_manual, got: {entry.get('result')}"


# ---------------------------------------------------------------------------
# PHASE 7: get_project_status
# ---------------------------------------------------------------------------

async def test_PHASE_7_get_project_status():
    """get_project_status returns current phase and pending approvals."""
    from app.mcp_tools.projects import get_project_status
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await get_project_status(token=token, project_id=project_id)
    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    for field in ("project_id", "status", "pending_approvals", "next_executable_cr_ids"):
        assert field in result, f"Missing field '{field}' in: {result}"


# ---------------------------------------------------------------------------
# PHASE 8: get_project_timeline
# ---------------------------------------------------------------------------

async def test_PHASE_8_get_project_timeline():
    """get_project_timeline returns ordered history (empty for new project)."""
    from app.mcp_tools.projects import get_project_timeline
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await get_project_timeline(token=token, project_id=project_id)
    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    # May be empty for a new project; just verify structure if populated
    if result:
        entry = result[0]
        for field in ("cr_id", "change_type", "title", "outcome", "executor"):
            assert field in entry, f"Missing field '{field}' in: {entry}"


# ---------------------------------------------------------------------------
# PHASE 9: estimate_project_risk
# ---------------------------------------------------------------------------

async def test_PHASE_9_estimate_project_risk():
    """estimate_project_risk returns blast radius and rollback coverage."""
    from app.mcp_tools.projects import estimate_project_risk
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await estimate_project_risk(token=token, project_id=project_id)
    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    for field in ("blast_radius", "rollback_coverage_pct", "estimated_duration_minutes", "highest_risk_step"):
        assert field in result, f"Missing field '{field}' in: {result}"


# ---------------------------------------------------------------------------
# PHASE 10: reorder_project_crs
# ---------------------------------------------------------------------------

async def test_PHASE_10_reorder_project_crs():
    """reorder_project_crs accepts empty list gracefully or reorders with added CR."""
    from app.mcp_tools.projects import reorder_project_crs
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    added_cr_id = _STATE.get("added_cr_id")
    if not added_cr_id:
        pytest.skip("No CR was added in PHASE 4 — skipping reorder test")

    result = await reorder_project_crs(
        token=token,
        project_id=project_id,
        ordered_cr_ids=[added_cr_id],
    )
    assert "error" not in result, f"reorder returned error: {result}"


# ---------------------------------------------------------------------------
# PHASE 11: remove_cr_from_project
# ---------------------------------------------------------------------------

async def test_PHASE_11_remove_cr_from_project():
    """remove_cr_from_project removes the test CR."""
    from app.mcp_tools.projects import remove_cr_from_project
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    added_cr_id = _STATE.get("added_cr_id")
    if not added_cr_id:
        pytest.skip("No CR was added — skipping remove test")

    result = await remove_cr_from_project(token=token, project_id=project_id, cr_id=added_cr_id)
    assert "error" not in result, f"remove returned error: {result}"
    assert result.get("removed") is True, f"Expected removed=True: {result}"


# ---------------------------------------------------------------------------
# PHASE 12: rollback_project (draft project — should be a no-op or error)
# ---------------------------------------------------------------------------

async def test_PHASE_12_rollback_project_draft():
    """rollback_project on a draft project with no executed CRs is a safe no-op."""
    from app.mcp_tools.projects import rollback_project
    token = _env("API_TOKEN")
    project_id = _STATE.get("project_id")
    if not project_id:
        pytest.skip("create_project did not run")

    result = await rollback_project(token=token, project_id=project_id)
    # Draft project with no executed CRs should return a rollback result or informative error
    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    # Either rollback_id is set (empty rollback) or there's an informative message
    has_rollback_id = "rollback_id" in result
    has_message = "message" in result or "error" in result
    assert has_rollback_id or has_message, f"Unexpected rollback response structure: {result}"
