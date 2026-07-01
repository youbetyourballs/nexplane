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
    # Reverse-tunnel config: tells the agent whether to open its outbound
    # tunnel and which destinations it may bridge (deny-by-default allowlist).
    tunnel_enabled: bool = False
    tunnel_allowlist: list[str] = []


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


class TunnelConfigUpdate(BaseModel):
    """Admin request to set an agent's reverse-tunnel config."""
    enabled: bool
    # Deny-by-default allowlist entries: "CIDR|IP|hostname:port|range|*".
    allowlist: list[str] = []


class AgentTunnelStatus(BaseModel):
    agent_id: uuid.UUID
    hostname: str
    tunnel_enabled: bool
    tunnel_allowlist: list[str]
    online: bool  # currently connected to this control plane's relay
