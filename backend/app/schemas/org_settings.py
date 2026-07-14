# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    agent_configured: bool = False
    updated_at: datetime | None = None
    agent_secret_plaintext: str | None = None
    pre_state_retention_days: int = 30


class AIKeyUpdate(BaseModel):
    api_key: str


class AgentSecretUpdate(BaseModel):
    secret: str
