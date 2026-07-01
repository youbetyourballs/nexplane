# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.execution_run import ExecutionStatus


class ExecutionRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    change_request_id: uuid.UUID
    workflow_id: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None
    result: dict
