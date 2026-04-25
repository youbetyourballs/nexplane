import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.change_plan import PlanGeneratedBy


class ChangePlanRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    change_request_id: uuid.UUID
    generated_steps: list
    preflight_checks: list
    blast_radius: dict
    rollback_plan: dict
    verification_plan: dict
    generated_by: PlanGeneratedBy
    created_at: datetime
