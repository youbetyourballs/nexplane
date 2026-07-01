# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from typing import Optional
from pydantic import BaseModel, field_validator


class MaintenanceWindowCreate(BaseModel):
    name:             str
    cron_schedule:    str
    duration_minutes: int = 60
    applies_to_tags:  Optional[list[str]] = None
    enabled:          bool = True

    @field_validator("cron_schedule")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError("cron_schedule must be a 5-field cron expression (e.g. '0 2 * * 6')")
        return v.strip()

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 1 or v > 10080:  # max 1 week
            raise ValueError("duration_minutes must be between 1 and 10080")
        return v


class MaintenanceWindowRead(MaintenanceWindowCreate):
    id: int
    organization_id: uuid.UUID

    model_config = {"from_attributes": True}


class MaintenanceWindowStatusRead(BaseModel):
    id: int
    is_open: bool
    next_open_at: Optional[str] = None
