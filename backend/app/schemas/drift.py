# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ResourceStateRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    asset_id: uuid.UUID
    surface_type: str
    state: dict
    captured_at: datetime
    source: str
    source_cr_id: Optional[uuid.UUID] = None
    accepted_by: Optional[uuid.UUID] = None
    accepted_at: Optional[datetime] = None
    acceptance_note: Optional[str] = None


class DriftPolicyCreate(BaseModel):
    name: str
    scope_type: str
    scope_value: str
    surface_types: list[str]
    poll_interval_seconds: int = 3600
    enabled: bool = True


class DriftPolicyRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    scope_type: str
    scope_value: str
    surface_types: list[str]
    poll_interval_seconds: int
    auto_created: bool
    enabled: bool
    source_cr_id: Optional[uuid.UUID] = None
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    last_checked_at: Optional[datetime] = None


class DriftPolicyUpdate(BaseModel):
    name: Optional[str] = None
    poll_interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    surface_types: Optional[list[str]] = None


class DriftEventRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    asset_id: uuid.UUID
    surface_type: str
    drift_policy_id: uuid.UUID
    baseline_state: dict
    observed_state: dict
    diff: dict
    severity: str
    detected_at: datetime
    status: str
    shadow_cr_id: Optional[uuid.UUID] = None
    resolved_by: Optional[uuid.UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    attested_suppress_until: Optional[datetime] = None


class DriftEventAcceptBody(BaseModel):
    note: str = ""


class DriftEventAttestBody(BaseModel):
    note: str = ""
    snooze_days: int = 7


class AssetDriftSummary(BaseModel):
    asset_id: uuid.UUID
    resource_states: list[ResourceStateRead]
    open_events: list[DriftEventRead]
