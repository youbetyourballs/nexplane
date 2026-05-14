import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status"), nullable=False, default=ProjectStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    ai_context: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    members: Mapped[list["ProjectChangeRequest"]] = relationship(
        "ProjectChangeRequest",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectChangeRequest.sequence_order",
    )


class ProjectChangeRequest(Base):
    __tablename__ = "project_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    change_request: Mapped["ChangeRequest"] = relationship("ChangeRequest")
