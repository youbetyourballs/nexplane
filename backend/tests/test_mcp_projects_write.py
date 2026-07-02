# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Unit tests for projects MCP write tools (tools 6-10):
  create_project, chat_with_project, add_cr_to_project,
  remove_cr_from_project, reorder_project_crs
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp_tools.projects as proj_module
from app.mcp_tools.projects import (
    create_project,
    chat_with_project,
    add_cr_to_project,
    remove_cr_from_project,
    reorder_project_crs,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(org_id=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.organization_id = org_id or uuid.uuid4()
    return u


def _make_auth_tuple(user, db=None, db_cm=None):
    if db is None:
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.delete = AsyncMock()
        db.refresh = AsyncMock(return_value=None)
    if db_cm is None:
        db_cm = AsyncMock()
        db_cm.__aexit__ = AsyncMock(return_value=None)
    return (user, db, db_cm)


# ── TOOL 6: create_project ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_project_returns_draft_status():
    """create_project returns a dict with id/name/goal/status=draft."""
    import app.models.project as proj_model_module

    org_id = uuid.uuid4()
    user = _make_user(org_id=org_id)

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(return_value=None)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    fake_id = uuid.uuid4()

    class FakeProject:
        def __init__(self, **kwargs):
            self.id = fake_id
            self.name = kwargs.get("name", "")
            self.goal = kwargs.get("goal", "")
            self.status = MagicMock()
            self.status.value = "draft"

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple), \
         patch.object(proj_model_module, "Project", FakeProject):
        result = await create_project("mytoken", "Harden Prod", "Harden all prod servers")

    assert result["name"] == "Harden Prod"
    assert result["goal"] == "Harden all prod servers"
    assert result["status"] == "draft"
    assert result["id"] == str(fake_id)


@pytest.mark.asyncio
async def test_create_project_with_description_and_template():
    """create_project accepts optional description and template."""
    import app.models.project as proj_model_module

    user = _make_user()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(return_value=None)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    class FakeProject:
        def __init__(self, **kwargs):
            self.id = uuid.uuid4()
            self.name = kwargs.get("name", "")
            self.goal = kwargs.get("goal", "")
            self.status = MagicMock()
            self.status.value = "draft"

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple), \
         patch.object(proj_model_module, "Project", FakeProject):
        result = await create_project(
            "tok", "My Project", "Goal text",
            description="Some description", template="cis_hardening"
        )

    assert result["status"] == "draft"
    assert "id" in result


# ── TOOL 7: chat_with_project ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_with_project_not_found():
    """chat_with_project returns error when project not found."""
    user = _make_user()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=proj_res)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await chat_with_project("tok", str(uuid.uuid4()), "Hello")

    assert result.get("error") == "Project not found"


@pytest.mark.asyncio
async def test_chat_with_project_no_ai_configured():
    """chat_with_project returns AI not configured fallback when no API key."""
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()
    project.goal = "Harden prod"
    project.name = "Harden"
    project.ai_context = []
    project.last_chat_summary = None

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    org_settings = MagicMock()
    org_settings.anthropic_api_key_encrypted = None
    org_settings.ai_providers_encrypted = None
    settings_res = MagicMock()
    settings_res.scalar_one_or_none.return_value = org_settings

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, settings_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await chat_with_project("tok", str(uuid.uuid4()), "Hello")

    assert result.get("reply") == "AI service not configured"
    assert result.get("proposed_crs") == []


# ── TOOL 8: add_cr_to_project ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_cr_to_project_project_not_found():
    user = _make_user()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=proj_res)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await add_cr_to_project("tok", str(uuid.uuid4()), str(uuid.uuid4()))

    assert result.get("error") == "Project not found"


@pytest.mark.asyncio
async def test_add_cr_to_project_cr_not_found():
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    cr_res = MagicMock()
    cr_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, cr_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await add_cr_to_project("tok", str(uuid.uuid4()), str(uuid.uuid4()))

    assert "error" in result
    assert "not found" in result["error"].lower() or "different" in result["error"].lower()


@pytest.mark.asyncio
async def test_add_cr_to_project_success_auto_sequence():
    """add_cr_to_project auto-assigns sequence_order = max+1 when not specified."""
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    cr = MagicMock()
    cr.id = uuid.uuid4()

    # Existing PCR with sequence_order=2; so new one should be 3
    existing_pcr = MagicMock()
    existing_pcr.sequence_order = 2

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    cr_res = MagicMock()
    cr_res.scalar_one_or_none.return_value = cr

    existing_res = MagicMock()
    existing_res.scalars.return_value.all.return_value = [existing_pcr]

    new_pcr_id = uuid.uuid4()

    # The tool does db.add(pcr) and db.refresh(pcr) — we capture pcr via db.add
    added_objects = []

    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, cr_res, existing_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    # Patch refresh to set a known id on whatever was added
    async def fake_refresh(obj):
        obj.id = new_pcr_id

    db.refresh = AsyncMock(side_effect=fake_refresh)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await add_cr_to_project("tok", str(project.id), str(cr.id))

    # sequence_order should be max(2)+1 = 3
    assert result.get("sequence_order") == 3
    assert result.get("pcr_id") == str(new_pcr_id)


# ── TOOL 9: remove_cr_from_project ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_cr_from_project_project_not_found():
    user = _make_user()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=proj_res)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await remove_cr_from_project("tok", str(uuid.uuid4()), str(uuid.uuid4()))

    assert result.get("error") == "Project not found"


@pytest.mark.asyncio
async def test_remove_cr_from_project_cr_not_member():
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    pcr_res = MagicMock()
    pcr_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, pcr_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await remove_cr_from_project("tok", str(uuid.uuid4()), str(uuid.uuid4()))

    assert result.get("error") == "CR is not a member of this project"


@pytest.mark.asyncio
async def test_remove_cr_from_project_success_renumbers():
    """remove_cr_from_project re-numbers remaining CRs starting at 1."""
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    pcr_to_delete = MagicMock()
    pcr_to_delete.id = uuid.uuid4()

    remaining_cr_id1 = uuid.uuid4()
    remaining_cr_id2 = uuid.uuid4()

    rem1 = MagicMock()
    rem1.sequence_order = 2
    rem1.change_request_id = remaining_cr_id1

    rem2 = MagicMock()
    rem2.sequence_order = 3
    rem2.change_request_id = remaining_cr_id2

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    pcr_res = MagicMock()
    pcr_res.scalar_one_or_none.return_value = pcr_to_delete

    remaining_res = MagicMock()
    remaining_res.scalars.return_value.all.return_value = [rem1, rem2]

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, pcr_res, remaining_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await remove_cr_from_project("tok", str(project.id), str(uuid.uuid4()))

    assert result.get("removed") is True
    seqs = [item["sequence_order"] for item in result["updated_sequence"]]
    assert seqs == [1, 2], f"Expected [1, 2] but got {seqs}"


# ── TOOL 10: reorder_project_crs ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reorder_project_crs_project_not_found():
    user = _make_user()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=proj_res)
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await reorder_project_crs("tok", str(uuid.uuid4()), [])

    assert result.get("error") == "Project not found"


@pytest.mark.asyncio
async def test_reorder_project_crs_missing_cr():
    """reorder_project_crs returns error when a cr_id is not in the project."""
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    pcr_res = MagicMock()
    pcr_res.scalars.return_value.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, pcr_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await reorder_project_crs("tok", str(uuid.uuid4()), [str(uuid.uuid4())])

    assert "error" in result
    assert "not in project" in result["error"]


@pytest.mark.asyncio
async def test_reorder_project_crs_success():
    """reorder_project_crs assigns 1-based sequence_order matching provided list."""
    user = _make_user()
    project = MagicMock()
    project.id = uuid.uuid4()

    cr_id_a = str(uuid.uuid4())
    cr_id_b = str(uuid.uuid4())

    pcr_a = MagicMock()
    pcr_a.change_request_id = uuid.UUID(cr_id_a)
    pcr_a.sequence_order = 2  # will be re-ordered to 1

    pcr_b = MagicMock()
    pcr_b.change_request_id = uuid.UUID(cr_id_b)
    pcr_b.sequence_order = 1  # will be re-ordered to 2

    proj_res = MagicMock()
    proj_res.scalar_one_or_none.return_value = project

    pcr_res = MagicMock()
    pcr_res.scalars.return_value.all.return_value = [pcr_a, pcr_b]

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=[proj_res, pcr_res])
    db_cm = AsyncMock()
    db_cm.__aexit__ = AsyncMock(return_value=None)

    auth_tuple = (user, db, db_cm)

    with patch("app.mcp_tools.projects._auth", return_value=auth_tuple):
        result = await reorder_project_crs("tok", str(project.id), [cr_id_a, cr_id_b])

    assert "updated_sequence" in result
    assert result["updated_sequence"][0] == {"cr_id": cr_id_a, "sequence_order": 1}
    assert result["updated_sequence"][1] == {"cr_id": cr_id_b, "sequence_order": 2}
    assert pcr_a.sequence_order == 1
    assert pcr_b.sequence_order == 2
