import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.change_request import ChangeType, RiskLevel, ChangeRequestStatus
from app.schemas.auth import UserRead


class ChangeRequestCreate(BaseModel):
    title: str
    description: str = ""
    change_type: ChangeType
    target_asset_ids: list[uuid.UUID]
    desired_outcome: dict


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
    risk_level: RiskLevel
    status: ChangeRequestStatus
    created_at: datetime
    updated_at: datetime
    requester: UserRead
    change_plan: "ChangePlanRead | None" = None
    approvals: "list[ApprovalRead]" = []
    execution_runs: "list[ExecutionRunRead]" = []


# Deferred imports to avoid circular refs
from app.schemas.change_plan import ChangePlanRead  # noqa: E402
from app.schemas.approval import ApprovalRead  # noqa: E402
from app.schemas.execution_run import ExecutionRunRead  # noqa: E402

ChangeRequestRead.model_rebuild()
