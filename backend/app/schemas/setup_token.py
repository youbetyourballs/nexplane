# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel


class SetupConsumeRequest(BaseModel):
    token: str
    instance_url: str
    admin_email: str
    admin_password: str
    admin_name: str
    org_name: str


class SetupConsumeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    org_id: uuid.UUID


class SetupTokenCreateRequest(BaseModel):
    instance_url: str


class SetupTokenCreateResponse(BaseModel):
    token: str
    setup_url: str
    expires_at: datetime
