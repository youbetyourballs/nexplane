# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel
from app.models.change_request import ChangeType, RiskLevel, ChangeRequestStatus
from app.schemas.auth import UserRead


class ChangeRequestCreate(BaseModel):
    title: str
    description: str = ""
    change_type: ChangeType
    target_asset_ids: list[uuid.UUID] = []
    desired_outcome: dict
    finding_ids: list[str] = []
    snapshot_before: bool = False
    verification_checks: list[dict] = []
    priority: str = "normal"
    emergency_reason: str | None = None
    access_expiry_hours: float | None = None
    scheduled_rollback_cr_id: uuid.UUID | None = None


class ChangeRequestSummary(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    change_type: ChangeType
    risk_level: RiskLevel
    status: ChangeRequestStatus
    created_at: datetime
    updated_at: datetime
    requester: UserRead
    application_sequence: int | None = None
    applied_at: datetime | None = None


class ChangeRequestRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    requester_id: uuid.UUID
    title: str
    description: str
    change_type: ChangeType
    target_asset_ids: list
    desired_outcome: dict
    finding_ids: list[str] = []
    snapshot_before: bool = False
    verification_checks: list[dict] = []
    batch_id: uuid.UUID | None = None
    priority: str = "normal"
    emergency_reason: str | None = None
    access_expiry_hours: float | None = None
    scheduled_rollback_cr_id: uuid.UUID | None = None
    artifact_refs: dict | None = None
    risk_level: RiskLevel
    status: ChangeRequestStatus
    created_at: datetime
    updated_at: datetime
    # FILO rollback stack fields
    application_sequence: int | None = None
    applied_at: datetime | None = None
    requester: UserRead
    change_plan: "ChangePlanRead | None" = None
    approvals: "list[ApprovalRead]" = []
    execution_runs: "list[ExecutionRunRead]" = []


class BatchCreateItem(BaseModel):
    title: str
    change_type: str
    target_asset_ids: list[str]
    desired_outcome: dict = {}
    finding_ids: list[str] = []
    snapshot_before: bool = False
    verification_checks: list[dict] = []


class BatchCreateRequest(BaseModel):
    items: list[BatchCreateItem]


class BatchCreateResponse(BaseModel):
    batch_id: uuid.UUID
    cr_ids: list[uuid.UUID]


class BulkApproveRequest(BaseModel):
    cr_ids: list[uuid.UUID]
    decision: str  # "approved" | "rejected"
    comment: str = ""


class OffboardUserPayload(BaseModel):
    target_email: str
    reason: Literal["resignation", "termination", "contract_end"]
    isolate_endpoints: bool = False
    notify_manager: bool = True
    manager_email: str | None = None


class OnboardUserPayload(BaseModel):
    target_email: str
    display_name: str
    department: str
    manager_email: str
    ad_ou: str | None = None
    ad_groups: list[str] = []
    okta_groups: list[str] = []
    google_org_unit: str | None = None
    github_teams: list[str] = []
    slack_channels: list[str] = []


# Deferred imports to avoid circular refs
from app.schemas.change_plan import ChangePlanRead  # noqa: E402
from app.schemas.approval import ApprovalRead  # noqa: E402
from app.schemas.execution_run import ExecutionRunRead  # noqa: E402

ChangeRequestRead.model_rebuild()
