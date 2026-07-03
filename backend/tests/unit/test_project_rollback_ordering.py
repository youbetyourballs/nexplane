# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for application_sequence-first ordering in project_rollback_service.initiate()."""
import uuid
import logging
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_member(seq_order: int, app_seq: int | None) -> MagicMock:
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.status = "completed"
    cr.application_sequence = app_seq
    cr.change_type = "apply_sysctl_hardening"
    m = MagicMock()
    m.change_request_id = cr.id
    m.sequence_order = seq_order
    m.change_request = cr
    return m


async def test_sorts_by_application_sequence_desc_when_available():
    """Members with application_sequence are sorted by it DESC (newest first)."""
    member_a = _make_member(seq_order=1, app_seq=100)  # executed first
    member_b = _make_member(seq_order=2, app_seq=200)  # executed second

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()
    mock_step_cls = MagicMock()

    with (
        patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep", mock_step_cls),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services import project_rollback_service as prs
        rollback, warnings = await prs.initiate(
            db=mock_db,
            project=mock_project,
            triggered_by_user_id=uuid.uuid4(),
            notes=None,
            cr_ids=None,
        )

        # ProjectRollbackStep was called with sequence_order=1 for member_b (app_seq=200, rolled back first)
        calls = mock_step_cls.call_args_list
        first_step_cr_id = calls[0].kwargs["change_request_id"]
        assert first_step_cr_id == member_b.change_request.id, (
            f"Expected member_b (app_seq=200) to be rolled back first, got {first_step_cr_id}"
        )


async def test_falls_back_to_sequence_order_when_app_seq_is_none():
    """Members without application_sequence fall back to sequence_order DESC."""
    member_a = _make_member(seq_order=1, app_seq=None)
    member_b = _make_member(seq_order=2, app_seq=None)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()
    mock_step_cls = MagicMock()

    with (
        patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep", mock_step_cls),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services import project_rollback_service as prs
        await prs.initiate(
            db=mock_db,
            project=mock_project,
            triggered_by_user_id=uuid.uuid4(),
            notes=None,
            cr_ids=None,
        )

        calls = mock_step_cls.call_args_list
        first_step_cr_id = calls[0].kwargs["change_request_id"]
        assert first_step_cr_id == member_b.change_request.id, (
            f"Expected member_b (seq_order=2) rolled back first, got {first_step_cr_id}"
        )


async def test_emits_warning_when_ordering_diverges(caplog):
    """When application_sequence order diverges from sequence_order, a warning is logged."""
    # member_a has seq_order=1 but app_seq=200 (executed second despite being planned first)
    # member_b has seq_order=2 but app_seq=100 (executed first despite being planned second)
    member_a = _make_member(seq_order=1, app_seq=200)
    member_b = _make_member(seq_order=2, app_seq=100)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    with (
        patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep"),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="app.services.project_rollback_service"):
            from app.services import project_rollback_service as prs
            await prs.initiate(
                db=mock_db,
                project=mock_project,
                triggered_by_user_id=uuid.uuid4(),
                notes=None,
                cr_ids=None,
            )

    assert any("diverges" in record.message for record in caplog.records), (
        "Expected a warning about ordering divergence, got: " + str([r.message for r in caplog.records])
    )
