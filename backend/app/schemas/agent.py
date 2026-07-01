# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.agent import OsType, AgentJobStatus


class AgentRegisterRequest(BaseModel):
    machine_id: str
    hostname: str
    os_type: OsType
    ip_addresses: list[str] = []
    os_version: str = ""
    agent_version: str = ""


class AgentRegisterResponse(BaseModel):
    agent_id: uuid.UUID
    asset_id: uuid.UUID


class AgentJobResponse(BaseModel):
    job_id: uuid.UUID
    command: str
    parameters: dict
    hmac_signature: str


class AgentJobResultRequest(BaseModel):
    agent_id: uuid.UUID
    status: AgentJobStatus
    result: dict | None = None
    error: str | None = None
