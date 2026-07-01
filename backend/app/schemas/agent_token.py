# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AgentTokenCreate(BaseModel):
    name: str = Field(..., max_length=255)
    expires_in_days: Optional[int] = None
    allowed_connector_types: list[str] = Field(default_factory=list)
    allowed_asset_tags: list[str] = Field(default_factory=list)
    allowed_cr_types: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)


class AgentTokenResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    created_at: datetime
    expires_at: Optional[datetime]
    revoked: bool
    last_used_at: Optional[datetime]
    allowed_connector_types: list[str]
    allowed_asset_tags: list[str]
    allowed_cr_types: list[str]
    allowed_roles: list[str]


class AgentTokenCreateResponse(AgentTokenResponse):
    token: str
