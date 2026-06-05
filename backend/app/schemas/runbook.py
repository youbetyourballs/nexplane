from __future__ import annotations
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Any


class AssetSelector(BaseModel):
    tags: list[str] = []
    asset_ids: list[UUID] = []
    environment: str | None = None


class RunbookStepCreate(BaseModel):
    step_number: int
    name: str
    type: str  # "change" | "condition" | "human_checkpoint" | "parallel_group"
    change_type: str | None = None
    parameters: dict[str, Any] | None = None
    asset_selector: AssetSelector | None = None
    condition_expr: str | None = None
    on_true_step: int | None = None
    on_false_step: int | None = None
    prompt: str | None = None
    required_role: str | None = None
    timeout_hours: int | None = None
    on_timeout: str | None = None  # "abort" | "continue"
    on_failure: str = "abort"
    parallel_steps: list[RunbookStepCreate] = []  # children for parallel_group


RunbookStepCreate.model_rebuild()


class RunbookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] = []
    auto_execute: bool = False
    cron_schedule: str | None = None
    steps: list[RunbookStepCreate]


class RunbookUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    auto_execute: bool | None = None
    cron_schedule: str | None = None
    steps: list[RunbookStepCreate] | None = None


class RunbookStepOut(RunbookStepCreate):
    id: UUID
    runbook_id: UUID
    parallel_steps: list[RunbookStepOut] = []

    model_config = {"from_attributes": True}


RunbookStepOut.model_rebuild()


class RunbookOut(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    version: int
    tags: list[str]
    is_seed: bool
    auto_execute: bool
    cron_schedule: str | None
    last_scheduled_run_at: datetime | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    steps: list[RunbookStepOut]

    model_config = {"from_attributes": True}


class TriggerRunbookRequest(BaseModel):
    context: dict[str, Any] = {}
    force: bool = False


class RunbookStepResultOut(BaseModel):
    id: UUID
    step_number: int
    step_name: str
    step_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    change_request_ids: list[str]
    result: dict[str, Any]
    error_message: str | None

    model_config = {"from_attributes": True}


class RunbookExecutionOut(BaseModel):
    id: UUID
    runbook_id: UUID
    runbook_version: int
    triggered_by: UUID
    triggered_at: datetime
    completed_at: datetime | None
    context: dict[str, Any]
    status: str
    current_step: int
    step_results: list[RunbookStepResultOut]

    model_config = {"from_attributes": True}


class HumanCheckpointResumeRequest(BaseModel):
    step_number: int
    action: str  # "resume" | "abort"
