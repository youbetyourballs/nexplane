# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class AccessReviewScope(BaseModel):
    connector_ids: list[uuid.UUID] | None = None
    groups: list[str] | None = None
    user_emails: list[str] | None = None


class AccessReviewCreate(BaseModel):
    title: str
    scope: AccessReviewScope


class AccessReviewDecisionItem(BaseModel):
    decision: Literal["keep", "revoke"]
    note: str = ""


class AccessReviewDecisionsSubmit(BaseModel):
    decisions: dict[str, AccessReviewDecisionItem]


class AccessReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    title: str
    scope: dict
    status: str
    collected_at: datetime | None = None
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    snapshot: dict | None = None
    decisions: dict | None = None
    created_by: uuid.UUID | None = None


class AccessReviewApproveOut(BaseModel):
    review_id: uuid.UUID
    status: str
    generated_change_requests: int
