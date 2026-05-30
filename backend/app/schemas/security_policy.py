# backend/app/schemas/security_policy.py
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any


class SoakSessionCreate(BaseModel):
    project_id: uuid.UUID
    policy_type: str = "seccomp"
    asset_ids: list[uuid.UUID]
    window_seconds: int = Field(default=600, ge=30, le=86400)


class SoakSessionRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    policy_type: str
    status: str
    window_seconds: int
    asset_ids: list[Any]
    raw_observations: dict
    synthesized_profile: dict | None
    baseline_delta: dict | None
    partial: bool
    cr_id: uuid.UUID | None
    started_at: datetime
    stopped_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SoakSessionStopBody(BaseModel):
    service_name: str


class AcceptDiffBody(BaseModel):
    service_name: str


class BaselineRead(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    policy_type: str
    profile: dict
    cr_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
