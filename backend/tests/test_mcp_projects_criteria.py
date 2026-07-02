# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid


@pytest.mark.asyncio
async def test_define_success_criteria_invalid_type(monkeypatch):
    from app.mcp_tools import projects as proj_module

    async def fake_auth(token):
        user = MagicMock()
        user.organization_id = uuid.uuid4()
        db = AsyncMock()
        db_cm = AsyncMock()
        db_cm.__aexit__ = AsyncMock(return_value=None)
        proj = MagicMock()
        proj.id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = proj
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return user, db, db_cm

    monkeypatch.setattr(proj_module, "_auth", fake_auth)
    result = await proj_module.define_success_criteria(
        "tok",
        str(uuid.uuid4()),
        [{"type": "invalid_type", "description": "bad", "assertion": {}}],
    )
    assert "error" in result
    assert "validation_errors" in result


@pytest.mark.asyncio
async def test_check_success_criteria_project_not_found(monkeypatch):
    from app.mcp_tools import projects as proj_module

    async def fake_auth(token):
        user = MagicMock()
        user.organization_id = uuid.uuid4()
        db = AsyncMock()
        db_cm = AsyncMock()
        db_cm.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)
        return user, db, db_cm

    monkeypatch.setattr(proj_module, "_auth", fake_auth)
    result = await proj_module.check_success_criteria(
        "tok", str(uuid.uuid4())
    )
    assert "error" in result
