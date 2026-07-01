# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.approval import ApprovalDecision
from app.schemas.auth import UserRead


class ApprovalCreate(BaseModel):
    decision: ApprovalDecision
    comment: str | None = None


class ApprovalRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    change_request_id: uuid.UUID
    approver_id: uuid.UUID
    decision: ApprovalDecision
    comment: str | None
    created_at: datetime
    approver: UserRead
