import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.project import ProjectStatus
from app.schemas.change_request import ChangeRequestSummary


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    goal: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    status: ProjectStatus | None = None


class ProjectRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    description: str
    goal: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ProjectRead):
    member_count: int = 0
    completed_count: int = 0


class ProjectMemberCreate(BaseModel):
    change_request_id: uuid.UUID
    sequence_order: int = 0
    depends_on: list[uuid.UUID] = []


class ProjectMemberUpdate(BaseModel):
    sequence_order: int | None = None
    depends_on: list[uuid.UUID] | None = None


class ProjectMemberRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    change_request_id: uuid.UUID
    sequence_order: int
    depends_on: list[uuid.UUID]
    change_request: ChangeRequestSummary
    eligible: bool = False


class ProjectDetailRead(ProjectRead):
    members: list[ProjectMemberRead] = []
    ai_context: list[dict] = []
