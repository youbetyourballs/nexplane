import uuid
import enum
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Enum as SAEnum, JSON, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ProjectRollbackStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    awaiting_user = "awaiting_user"
    completed = "completed"
    failed = "failed"


class ProjectRollbackTrigger(str, enum.Enum):
    manual = "manual"
    execution_failure = "execution_failure"
    soak_health_check = "soak_health_check"


class RollbackStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    skipped = "skipped"
    completed = "completed"
    failed = "failed"
    awaiting_user = "awaiting_user"


class RollbackKind(str, enum.Enum):
    standard = "standard"
    reconstitution = "reconstitution"
    permanent_no_backup = "permanent_no_backup"


class ProjectRollback(Base):
    __tablename__ = "project_rollbacks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    status: Mapped[ProjectRollbackStatus] = mapped_column(
        SAEnum(ProjectRollbackStatus, name="project_rollback_status"), nullable=False,
        default=ProjectRollbackStatus.pending,
    )
    trigger: Mapped[ProjectRollbackTrigger] = mapped_column(
        SAEnum(ProjectRollbackTrigger, name="project_rollback_trigger"), nullable=False,
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    triggered_by_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["ProjectRollbackStep"]] = relationship(
        "ProjectRollbackStep",
        back_populates="rollback",
        cascade="all, delete-orphan",
        order_by="ProjectRollbackStep.sequence_order",
    )


class ProjectRollbackStep(Base):
    __tablename__ = "project_rollback_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_rollback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_rollbacks.id"), nullable=False,
    )
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False,
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RollbackStepStatus] = mapped_column(
        SAEnum(RollbackStepStatus, name="rollback_step_status"), nullable=False,
        default=RollbackStepStatus.pending,
    )
    rollback_kind: Mapped[RollbackKind] = mapped_column(
        SAEnum(RollbackKind, name="rollback_kind"), nullable=False,
        default=RollbackKind.standard,
    )
    backup_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    rollback: Mapped["ProjectRollback"] = relationship("ProjectRollback", back_populates="steps")
