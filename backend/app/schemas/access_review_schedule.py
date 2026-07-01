# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AccessReviewScheduleBase(BaseModel):
    frequency_days: int
    scope: str = "all_users"
    reviewer_assignment_rule: str = "direct_manager"
    enabled: bool = True


class AccessReviewScheduleCreate(AccessReviewScheduleBase):
    pass


class AccessReviewScheduleRead(AccessReviewScheduleBase):
    id: uuid.UUID
    last_review_created_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
