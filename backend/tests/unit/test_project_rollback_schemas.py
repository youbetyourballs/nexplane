# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.schemas.project_rollback import (
    ProjectRollbackRead, ProjectRollbackStepRead,
    RollbackInitRequest, StepDecisionRequest,
)
import uuid
from datetime import datetime, timezone


def test_rollback_read_schema():
    data = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": "pending",
        "trigger": "manual",
        "triggered_by_user_id": None,
        "triggered_by_cr_id": None,
        "current_step": 0,
        "notes": None,
        "created_at": datetime.now(timezone.utc),
        "started_at": None,
        "paused_at": None,
        "completed_at": None,
        "steps": [],
    }
    r = ProjectRollbackRead(**data)
    assert r.status == "pending"


def test_init_request_cr_ids_optional():
    r = RollbackInitRequest(notes="test")
    assert r.cr_ids is None

    r2 = RollbackInitRequest(cr_ids=[uuid.uuid4()])
    assert len(r2.cr_ids) == 1


def test_step_decision_valid_actions():
    for action in ("skip", "retry", "mark_done"):
        r = StepDecisionRequest(action=action)
        assert r.action == action
