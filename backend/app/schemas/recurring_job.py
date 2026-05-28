import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.recurring_job import RecurringJobType


class RecurringJobCreate(BaseModel):
    name: str
    job_type: RecurringJobType
    connector_id: uuid.UUID | None = None
    action_id: str
    parameters: dict = {}
    target_description: str
    cron_expression: str
    schedule_preset: str | None = None
    schedule_hour: int | None = None


class RecurringJobUpdate(BaseModel):
    name: str | None = None
    connector_id: uuid.UUID | None = None
    action_id: str | None = None
    parameters: dict | None = None
    target_description: str | None = None
    cron_expression: str | None = None
    schedule_preset: str | None = None
    schedule_hour: int | None = None
    enabled: bool | None = None


class RecurringJobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    job_type: RecurringJobType
    connector_id: uuid.UUID | None
    action_id: str
    parameters: dict
    target_description: str
    cron_expression: str
    schedule_preset: str | None
    schedule_hour: int | None
    enabled: bool
    last_run_at: datetime | None
    last_cr_id: uuid.UUID | None
    next_run_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime
