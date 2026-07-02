# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Tests for MCP project execution tools: execute_project_phase, rollback_project,
materialize_project_plan."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    return u


def _make_db_cm(project=None, side_effects=None):
    """Return (auth_tuple, db) ready for patching."""
    db = AsyncMock()
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)
    user = _make_user()

    if side_effects is not None:
        db.execute = AsyncMock(side_effect=side_effects)
    elif project is not None:
        r = MagicMock()
        r.scalar_one_or_none.return_value = project
        db.execute = AsyncMock(return_value=r)
    else:
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=r)

    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return (user, db, db_cm), db, user


# ── execute_project_phase ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_project_phase_no_eligible_crs():
    """When no CRs are approved, started_executions is empty."""
    from app.mcp_tools.projects import execute_project_phase

    proj = MagicMock()
    proj.id = uuid.uuid4()
    member = MagicMock()
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.status.value = "draft"
    member.change_request = cr
    member.change_request_id = cr.id
    member.id = uuid.uuid4()
    member.depends_on = []
    proj.members = [member]

    auth_tuple, db, user = _make_db_cm(project=proj)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await execute_project_phase("tok", str(uuid.uuid4()))

    assert result["started_executions"] == []


@pytest.mark.asyncio
async def test_execute_project_phase_project_not_found():
    """Returns error dict when project not found."""
    from app.mcp_tools.projects import execute_project_phase

    auth_tuple, db, user = _make_db_cm(project=None)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await execute_project_phase("tok", str(uuid.uuid4()))

    assert "error" in result


@pytest.mark.asyncio
async def test_execute_project_phase_explicit_cr_ids_not_found():
    """Returns error when explicit cr_ids don't match any project members."""
    from app.mcp_tools.projects import execute_project_phase

    proj = MagicMock()
    proj.id = uuid.uuid4()
    proj.members = []
    auth_tuple, db, user = _make_db_cm(project=proj)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await execute_project_phase("tok", str(uuid.uuid4()), cr_ids=[str(uuid.uuid4())])

    assert "error" in result
    assert result["started_executions"] == []


@pytest.mark.asyncio
async def test_execute_project_phase_approved_no_deps():
    """Approved CR with no deps is started via ChangeExecutionService.start."""
    from app.mcp_tools.projects import execute_project_phase

    proj = MagicMock()
    proj.id = uuid.uuid4()
    member = MagicMock()
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.status.value = "approved"
    member.change_request = cr
    member.change_request_id = cr.id
    member.id = uuid.uuid4()
    member.depends_on = []
    proj.members = [member]

    auth_tuple, db, user = _make_db_cm(project=proj)

    fake_run = MagicMock()
    fake_run.id = uuid.uuid4()

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        with patch(
            "app.services.change_execution_service.ChangeExecutionService.start",
            new_callable=AsyncMock,
            return_value=fake_run,
        ):
            result = await execute_project_phase("tok", str(uuid.uuid4()))

    assert len(result["started_executions"]) == 1
    assert result["started_executions"][0]["status"] == "executing"


@pytest.mark.asyncio
async def test_execute_project_phase_start_exception_goes_to_skipped():
    """If ChangeExecutionService.start raises, CR goes to skipped."""
    from app.mcp_tools.projects import execute_project_phase

    proj = MagicMock()
    proj.id = uuid.uuid4()
    member = MagicMock()
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.status.value = "approved"
    member.change_request = cr
    member.change_request_id = cr.id
    member.id = uuid.uuid4()
    member.depends_on = []
    proj.members = [member]

    auth_tuple, db, user = _make_db_cm(project=proj)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        with patch(
            "app.services.change_execution_service.ChangeExecutionService.start",
            new_callable=AsyncMock,
            side_effect=Exception("workflow error"),
        ):
            result = await execute_project_phase("tok", str(uuid.uuid4()))

    assert result["started_executions"] == []
    assert len(result["skipped"]) == 1
    assert "workflow error" in result["skipped"][0]["reason"]


# ── rollback_project ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_project_not_found():
    """Returns error when project not found."""
    from app.mcp_tools.projects import rollback_project

    auth_tuple, db, user = _make_db_cm(project=None)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await rollback_project("tok", str(uuid.uuid4()))

    assert "error" in result


@pytest.mark.asyncio
async def test_rollback_project_success():
    """Returns rollback_id on success."""
    from app.mcp_tools.projects import rollback_project

    proj = MagicMock()
    proj.id = uuid.uuid4()
    proj.members = []
    auth_tuple, db, user = _make_db_cm(project=proj)

    rollback_id = uuid.uuid4()
    rb = MagicMock()
    rb.id = rollback_id

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        with patch(
            "app.services.project_rollback_service.initiate",
            new_callable=AsyncMock,
            return_value=(rb, []),
        ):
            result = await rollback_project("tok", str(uuid.uuid4()))

    assert result["rollback_initiated"] is True
    assert result["rollback_id"] == str(rollback_id)
    assert result["note"] is None


@pytest.mark.asyncio
async def test_rollback_project_to_cr_id_note():
    """When to_cr_id provided, note field mentions partial rollback."""
    from app.mcp_tools.projects import rollback_project

    proj = MagicMock()
    proj.id = uuid.uuid4()
    proj.members = []
    auth_tuple, db, user = _make_db_cm(project=proj)

    rb = MagicMock()
    rb.id = uuid.uuid4()

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        with patch(
            "app.services.project_rollback_service.initiate",
            new_callable=AsyncMock,
            return_value=(rb, []),
        ):
            result = await rollback_project("tok", str(uuid.uuid4()), to_cr_id=str(uuid.uuid4()))

    assert result["note"] is not None
    assert "partial rollback" in result["note"]


# ── materialize_project_plan ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_materialize_project_not_found():
    """Returns error dict when project not found."""
    from app.mcp_tools.projects import materialize_project_plan

    auth_tuple, db, user = _make_db_cm(project=None)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await materialize_project_plan("tok", str(uuid.uuid4()), [])

    assert "error" in result


@pytest.mark.asyncio
async def test_materialize_missing_change_type():
    """Returns error entry when change_type is missing from proposed CR."""
    from app.mcp_tools.projects import materialize_project_plan

    proj = MagicMock()
    proj.id = uuid.uuid4()

    mock_proj = MagicMock()
    mock_proj.scalar_one_or_none.return_value = proj
    mock_existing = MagicMock()
    mock_existing.scalars.return_value.all.return_value = []

    auth_tuple, db, user = _make_db_cm(side_effects=[mock_proj, mock_existing])

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await materialize_project_plan(
            "tok",
            str(uuid.uuid4()),
            [{"target_assets": ["some-host"], "desired_outcome": {}}],
        )

    assert result["created_crs"] == []
    assert len(result["errors"]) == 1
    assert "missing change_type" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_materialize_missing_asset():
    """Returns error entry when asset not found by name or hostname."""
    from app.mcp_tools.projects import materialize_project_plan

    proj = MagicMock()
    proj.id = uuid.uuid4()

    mock_proj = MagicMock()
    mock_proj.scalar_one_or_none.return_value = proj
    mock_existing = MagicMock()
    mock_existing.scalars.return_value.all.return_value = []
    mock_asset_none = MagicMock()
    mock_asset_none.scalar_one_or_none.return_value = None

    auth_tuple, db, user = _make_db_cm(
        side_effects=[mock_proj, mock_existing, mock_asset_none, mock_asset_none]
    )

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await materialize_project_plan(
            "tok",
            str(uuid.uuid4()),
            [{"change_type": "patch_packages", "target_assets": ["nonexistent-host"],
              "desired_outcome": {}, "seq": 1, "depends_on": []}],
        )

    assert result["created_crs"] == []
    assert len(result["errors"]) == 1
    assert "not found" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_materialize_missing_target_assets():
    """Returns error entry when target_assets is empty."""
    from app.mcp_tools.projects import materialize_project_plan

    proj = MagicMock()
    proj.id = uuid.uuid4()

    mock_proj = MagicMock()
    mock_proj.scalar_one_or_none.return_value = proj
    mock_existing = MagicMock()
    mock_existing.scalars.return_value.all.return_value = []

    auth_tuple, db, user = _make_db_cm(side_effects=[mock_proj, mock_existing])

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await materialize_project_plan(
            "tok",
            str(uuid.uuid4()),
            [{"change_type": "patch_packages", "target_assets": [], "desired_outcome": {}}],
        )

    assert result["created_crs"] == []
    assert len(result["errors"]) == 1
    assert "missing target_assets" in result["errors"][0]["error"]
