# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TokenCreate(BaseModel):
    name: str
    expires_at: Optional[datetime] = None


class TokenRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    revoked: bool
    created_at: datetime


class TokenCreatedResponse(BaseModel):
    """Returned once on creation. raw_token is never stored and cannot be recovered."""
    id: uuid.UUID
    name: str
    raw_token: str
    expires_at: Optional[datetime]
    created_at: datetime
