# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for rollback_project to_cr_id enforcement."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_member(seq_order: int, status: str = "completed") -> MagicMock:
    cr_id = uuid.uuid4()
    cr = MagicMock()
    cr.id = cr_id
    cr.status = status
    cr.application_sequence = seq_order * 100
    m = MagicMock()
    m.change_request_id = cr_id
    m.sequence_order = seq_order
    m.change_request = cr
    return m


async def test_to_cr_id_none_passes_none_to_initiate():
    """When to_cr_id is None, cr_ids=None is passed (full rollback)."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1)
    member_b = _make_member(seq_order=2)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a, member_b]

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    # Patch _auth to return a User principal
    from app.models.user import User
    principal = MagicMock(spec=User)
    principal.id = uuid.uuid4()
    principal.organization_id = mock_project.organization_id
    db_cm = MagicMock()
    db_cm.__aexit__ = AsyncMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=mock_project)
    ))

    with (
        patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )),
        patch("app.services.project_rollback_service.initiate", new=AsyncMock(
            return_value=(mock_rollback, [])
        )) as mock_initiate,
    ):
        from app.mcp_tools.projects import rollback_project
        result = await rollback_project(token=token, project_id=project_id)

    call_kwargs = mock_initiate.call_args.kwargs
    assert call_kwargs["cr_ids"] is None
    assert "rollback_id" in result


async def test_to_cr_id_not_in_project_returns_error():
    """When to_cr_id is not a member of the project, return cr_not_in_project."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a]

    foreign_cr_id = str(uuid.uuid4())

    with patch("app.mcp_tools.projects._auth"):
        from app.models.user import User
        principal = MagicMock(spec=User)
        principal.id = uuid.uuid4()
        principal.organization_id = mock_project.organization_id
        db_cm = MagicMock()
        db_cm.__aexit__ = AsyncMock()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_project)
        ))

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(
                token=token, project_id=project_id, to_cr_id=foreign_cr_id
            )

    assert result["error"] == "cr_not_in_project"
    assert result["cr_id"] == foreign_cr_id


async def test_to_cr_id_not_executed_returns_error():
    """When to_cr_id references a non-completed CR, return cr_not_executed."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1, status="draft")  # not completed
    to_cr_id = str(member_a.change_request_id)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a]

    with patch("app.mcp_tools.projects._auth"):
        from app.models.user import User
        principal = MagicMock(spec=User)
        principal.id = uuid.uuid4()
        principal.organization_id = mock_project.organization_id
        db_cm = MagicMock()
        db_cm.__aexit__ = AsyncMock()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_project)
        ))

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(
                token=token, project_id=project_id, to_cr_id=to_cr_id
            )

    assert result["error"] == "cr_not_executed"
    assert result["cr_id"] == to_cr_id


async def test_to_cr_id_filters_to_sequence_order_gte():
    """When to_cr_id is valid, only CRs with sequence_order >= target are rolled back."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_1 = _make_member(seq_order=1)
    member_2 = _make_member(seq_order=2)
    member_3 = _make_member(seq_order=3)
    to_cr_id = str(member_2.change_request_id)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_1, member_2, member_3]

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    from app.models.user import User
    principal = MagicMock(spec=User)
    principal.id = uuid.uuid4()
    principal.organization_id = mock_project.organization_id
    db_cm = MagicMock()
    db_cm.__aexit__ = AsyncMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=mock_project)
    ))

    with (
        patch("app.services.project_rollback_service.initiate", new=AsyncMock(
            return_value=(mock_rollback, [])
        )) as mock_initiate,
        patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )),
    ):
        from app.mcp_tools.projects import rollback_project
        result = await rollback_project(
            token=token, project_id=project_id, to_cr_id=to_cr_id
        )

    call_kwargs = mock_initiate.call_args.kwargs
    filtered_ids = set(call_kwargs["cr_ids"])
    # member_2 and member_3 should be included; member_1 should not
    assert member_2.change_request_id in filtered_ids
    assert member_3.change_request_id in filtered_ids
    assert member_1.change_request_id not in filtered_ids
    assert "rollback_id" in result
