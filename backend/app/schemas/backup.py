# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/schemas/backup.py
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.backup_target import BackupTargetStatus


class BackupTargetCreate(BaseModel):
    target_description: str
    expected_cadence_hours: int = 24
    recurring_job_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    backup_tier: str = "machine"
    capture_strategy: str = "ebs_snapshot"
    storage_id: uuid.UUID | None = None


class BackupTargetUpdate(BaseModel):
    target_description: str | None = None
    expected_cadence_hours: int | None = None
    asset_id: uuid.UUID | None = None
    backup_tier: str | None = None
    capture_strategy: str | None = None
    storage_id: uuid.UUID | None = None
    recurring_job_id: uuid.UUID | None = None


class BackupTargetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    recurring_job_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    target_description: str
    expected_cadence_hours: int
    last_successful_backup_cr_id: uuid.UUID | None
    last_successful_at: datetime | None
    status: BackupTargetStatus
    backup_tier: str
    capture_strategy: str
    storage_id: uuid.UUID | None
    created_at: datetime


class BackupHistoryRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    change_type: str
    status: str
    artifact_refs: dict | None
    created_at: datetime


class RestoreCrCreate(BaseModel):
    source_cr_id: uuid.UUID
    target_description: str
    restore_type: str = "full"
    notes: str = ""


class BackupContextRead(BaseModel):
    has_backup: bool
    last_successful_at: datetime | None = None
    artifact: dict | None = None
    backup_cr_id: uuid.UUID | None = None
    overdue: bool = False


class StrategyRecommendation(BaseModel):
    capture_strategy: str
    backup_tier: str
    reason: str
    alternatives: list[dict]


# ---------------------------------------------------------------------------
# BackupStorage schemas
# ---------------------------------------------------------------------------
from typing import Any


class BackupStorageCreate(BaseModel):
    name: str
    storage_type: str  # "s3" | "gcs" | "azure_blob" | "nfs" | "local"
    config: dict[str, Any]
    is_org_default: bool = False


class BackupStorageRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    storage_type: str
    is_org_default: bool
    created_at: str


class BackupStorageUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    is_org_default: bool | None = None


class RecoveryTokenRead(BaseModel):
    token: str
    asset_id: str
    expires_at: str
    token_id: str
