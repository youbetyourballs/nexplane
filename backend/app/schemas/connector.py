# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.connector import ConnectorType, ConnectorStatus
from app.schemas.asset import AssetRead


class ConnectorCreate(BaseModel):
    connector_type: ConnectorType
    name: str
    scoped_permissions: dict = {}
    network_path: str = "direct"
    network_tls_skip_verify: bool = False


class ConnectorRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    connector_type: ConnectorType
    name: str
    status: ConnectorStatus
    scoped_permissions: dict
    network_path: str
    network_tls_skip_verify: bool
    created_at: datetime


class ConnectorTestResult(BaseModel):
    success: bool
    latency_ms: int
    message: str
    details: dict = {}


class IngestResponse(BaseModel):
    created: int
    updated: int
    assets: list[AssetRead]
