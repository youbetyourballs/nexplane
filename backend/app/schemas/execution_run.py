import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.execution_run import ExecutionStatus


class ExecutionRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    change_request_id: uuid.UUID
    workflow_id: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None
    result: dict
