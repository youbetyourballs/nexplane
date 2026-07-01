# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field

class CampaignCreate(BaseModel):
    title: str
    cve_id: Optional[str] = None
    target_asset_ids: list[uuid.UUID]
    batch_size: int = Field(default=5, ge=1, le=100)
    health_gate_seconds: int = Field(default=120, ge=10)
    health_endpoint: str = "/health"
    abort_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    rollout_strategy: str = "rolling"

class BatchResult(BaseModel):
    batch_index: int
    asset_ids: list[str]
    status: str
    cr_ids: list[str] = []
    health_check_result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class CampaignRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    cve_id: Optional[str]
    target_asset_ids: list[Any]
    batch_size: int
    health_gate_seconds: int
    health_endpoint: str
    abort_threshold: float
    rollout_strategy: str
    status: str
    batches: list[Any]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    total_assets: int = 0
    passed_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
