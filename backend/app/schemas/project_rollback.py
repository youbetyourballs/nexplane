# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

from app.models.project_rollback import (
    ProjectRollbackStatus, ProjectRollbackTrigger,
    RollbackStepStatus, RollbackKind,
)


class ProjectRollbackStepRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_rollback_id: uuid.UUID
    change_request_id: uuid.UUID
    sequence_order: int
    status: RollbackStepStatus
    rollback_kind: RollbackKind
    backup_cr_id: uuid.UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    result: dict | None


class ProjectRollbackRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    status: ProjectRollbackStatus
    trigger: ProjectRollbackTrigger
    triggered_by_user_id: uuid.UUID | None
    triggered_by_cr_id: uuid.UUID | None
    current_step: int
    notes: str | None
    created_at: datetime
    started_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    steps: list[ProjectRollbackStepRead] = []


class RollbackInitRequest(BaseModel):
    notes: str | None = None
    cr_ids: list[uuid.UUID] | None = None


class RollbackInitResponse(BaseModel):
    rollback: ProjectRollbackRead
    warnings: list[str] = []


class StepDecisionRequest(BaseModel):
    action: Literal["skip", "retry", "mark_done"]
