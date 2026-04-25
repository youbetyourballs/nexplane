import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.approval import ApprovalDecision
from app.schemas.auth import UserRead


class ApprovalCreate(BaseModel):
    decision: ApprovalDecision
    comment: str | None = None


class ApprovalRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    change_request_id: uuid.UUID
    approver_id: uuid.UUID
    decision: ApprovalDecision
    comment: str | None
    created_at: datetime
    approver: UserRead
