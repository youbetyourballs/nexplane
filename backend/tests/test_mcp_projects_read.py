# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Unit tests for projects MCP read tools:
  list_projects, get_project, get_project_status, get_project_timeline, estimate_project_risk
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp_tools.projects  # noqa: F401 — ensure tools are registered


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    return u


def _make_mock_project(name="Test Project", status_val="draft", org_id=None):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.organization_id = org_id or uuid.uuid4()
    p.name = name
    p.goal = "Harden prod servers"
    p.description = ""
    p.status = MagicMock()
    p.status.value = status_val
    p.template = None
    p.last_chat_summary = None
    p.members = []
    p.rollbacks = []
    p.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    p.ai_context = []
    return p


def _make_mock_cr(status_val="draft", change_type="patch_os", asset_ids=None):
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.title = f"CR {change_type}"
    cr.change_type = change_type
    cr.status = MagicMock()
    cr.status.value = status_val
    cr.target_asset_ids = asset_ids or []
    cr.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    cr.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return cr


def _make_mock_pcr(cr, sequence_order=0, depends_on=None):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.change_request = cr
    m.sequence_order = sequence_order
    m.depends_on = depends_on or []
    return m


def _make_auth(user, project=None, projects=None):
    """Return a fake _auth tuple and a mock db that returns the given project(s)."""
    mock_db = AsyncMock()
    mock_db_cm = AsyncMock()
    mock_db_cm.__aexit__ = AsyncMock(return_value=None)

    if projects is not None:
        # list query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = projects
        mock_db.execute = AsyncMock(return_value=mock_result)
    elif project is not None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = project
        mock_db.execute = AsyncMock(return_value=mock_result)
    else:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

    return (user, mock_db, mock_db_cm)


# ── list_projects ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_projects_empty():
    """list_projects returns [] when no projects exist."""
    from app.mcp_tools.projects import list_projects

    user = _make_user()
    auth_tuple = _make_auth(user, projects=[])

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await list_projects("tok")

    assert result == []


@pytest.mark.asyncio
async def test_list_projects_returns_summary():
    """list_projects returns summary rows for each project."""
    from app.mcp_tools.projects import list_projects

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project("Prod Hardening", "in_progress", org_id)
    auth_tuple = _make_auth(user, projects=[p])

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await list_projects("tok")

    assert len(result) == 1
    assert result[0]["name"] == "Prod Hardening"
    assert result[0]["status"] == "in_progress"
    assert result[0]["cr_count"] == 0


@pytest.mark.asyncio
async def test_list_projects_invalid_status():
    """list_projects returns error dict for invalid status value."""
    from app.mcp_tools.projects import list_projects

    user = _make_user()
    auth_tuple = _make_auth(user, projects=[])

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await list_projects("tok", status="bogus")

    assert len(result) == 1
    assert "error" in result[0]


# ── get_project ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_project_not_found():
    """get_project returns error when project does not exist."""
    from app.mcp_tools.projects import get_project

    user = _make_user()
    auth_tuple = _make_auth(user)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project("tok", str(uuid.uuid4()))

    assert "error" in result


@pytest.mark.asyncio
async def test_get_project_returns_detail():
    """get_project returns full detail including members list."""
    from app.mcp_tools.projects import get_project

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project("My Project", "draft", org_id)
    cr = _make_mock_cr("awaiting_approval", "patch_os", ["asset-1"])
    pcr = _make_mock_pcr(cr, sequence_order=0)
    p.members = [pcr]

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project("tok", str(p.id))

    assert result["name"] == "My Project"
    assert len(result["members"]) == 1
    assert result["members"][0]["change_type"] == "patch_os"
    assert len(result["pending_approvals"]) == 1


# ── get_project_status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_project_status_not_found():
    """get_project_status returns error when project not found."""
    from app.mcp_tools.projects import get_project_status

    user = _make_user()
    auth_tuple = _make_auth(user)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project_status("tok", str(uuid.uuid4()))

    assert "error" in result


@pytest.mark.asyncio
async def test_get_project_status_approved_no_deps_is_executable():
    """An approved CR with no deps appears in next_executable_cr_ids."""
    from app.mcp_tools.projects import get_project_status

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project(org_id=org_id)
    cr = _make_mock_cr("approved", "patch_os")
    pcr = _make_mock_pcr(cr, depends_on=[])
    p.members = [pcr]

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project_status("tok", str(p.id))

    assert str(cr.id) in result["next_executable_cr_ids"]
    assert result["blocking_crs"] == []


@pytest.mark.asyncio
async def test_get_project_status_dep_blocks_execution():
    """An approved CR with an unmet dep appears in blocking_crs."""
    from app.mcp_tools.projects import get_project_status

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project(org_id=org_id)

    cr1 = _make_mock_cr("draft", "patch_os")
    pcr1 = _make_mock_pcr(cr1)

    cr2 = _make_mock_cr("approved", "patch_packages")
    pcr2 = _make_mock_pcr(cr2, depends_on=[str(pcr1.id)])
    p.members = [pcr1, pcr2]

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project_status("tok", str(p.id))

    assert len(result["blocking_crs"]) == 1
    assert result["blocking_crs"][0]["cr_id"] == str(cr2.id)
    assert str(cr2.id) not in result["next_executable_cr_ids"]


# ── get_project_timeline ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_project_timeline_not_found():
    """get_project_timeline returns error list when project not found."""
    from app.mcp_tools.projects import get_project_timeline

    user = _make_user()
    auth_tuple = _make_auth(user)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project_timeline("tok", str(uuid.uuid4()))

    assert len(result) == 1
    assert "error" in result[0]


@pytest.mark.asyncio
async def test_get_project_timeline_returns_ordered_entries():
    """get_project_timeline returns one entry per CR with outcome."""
    from app.mcp_tools.projects import get_project_timeline

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project(org_id=org_id)

    cr = _make_mock_cr("completed", "patch_os")
    pcr = _make_mock_pcr(cr, sequence_order=1)
    p.members = [pcr]

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await get_project_timeline("tok", str(p.id))

    assert len(result) == 1
    assert result[0]["outcome"] == "completed"
    assert result[0]["rolled_back"] is False
    assert result[0]["sequence_order"] == 1


# ── estimate_project_risk ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_estimate_project_risk_not_found():
    """estimate_project_risk returns error when project not found."""
    from app.mcp_tools.projects import estimate_project_risk

    user = _make_user()
    auth_tuple = _make_auth(user)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await estimate_project_risk("tok", str(uuid.uuid4()))

    assert "error" in result


@pytest.mark.asyncio
async def test_estimate_project_risk_empty_project():
    """estimate_project_risk returns 100% coverage and 0 assets for empty project."""
    from app.mcp_tools.projects import estimate_project_risk

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project(org_id=org_id)
    p.members = []

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await estimate_project_risk("tok", str(p.id))

    assert result["blast_radius_assets"] == 0
    assert result["total_crs"] == 0
    assert result["rollback_coverage_pct"] == 100.0
    assert result["highest_risk_step"] is None


@pytest.mark.asyncio
async def test_estimate_project_risk_calculates_blast_radius():
    """estimate_project_risk counts distinct asset IDs across all CRs."""
    from app.mcp_tools.projects import estimate_project_risk

    org_id = uuid.uuid4()
    user = _make_user(org_id)
    p = _make_mock_project(org_id=org_id)

    cr1 = _make_mock_cr("approved", "patch_os", ["asset-a", "asset-b"])
    cr2 = _make_mock_cr("approved", "patch_packages", ["asset-b", "asset-c"])
    pcr1 = _make_mock_pcr(cr1)
    pcr2 = _make_mock_pcr(cr2)
    p.members = [pcr1, pcr2]

    auth_tuple = _make_auth(user, project=p)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        with patch("app.services.manifest_builder.get_manifest", return_value=[], create=True):
            result = await estimate_project_risk("tok", str(p.id))

    # 3 distinct assets: a, b, c
    assert result["blast_radius_assets"] == 3
    assert result["total_crs"] == 2
    assert result["estimated_duration_minutes"] == 20  # 10 + 10
    assert result["highest_risk_step"] is not None
