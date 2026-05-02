import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator

VALID_INTERVALS = {1, 6, 24, 168}


class ScheduledIngestWrite(BaseModel):
    interval_hours: int
    action_id: str

    @field_validator("interval_hours")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v not in VALID_INTERVALS:
            raise ValueError(f"interval_hours must be one of {sorted(VALID_INTERVALS)}")
        return v


class ScheduledIngestRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    connector_id: uuid.UUID
    action_id: str
    interval_hours: int
    enabled: bool
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_error: str | None
    next_run_at: datetime | None
    created_at: datetime
