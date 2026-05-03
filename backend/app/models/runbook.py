import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tags: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    steps: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep",
        back_populates="runbook",
        order_by="RunbookStep.step_number",
        cascade="all, delete-orphan",
        foreign_keys="RunbookStep.runbook_id",
    )
    executions: Mapped[list["RunbookExecution"]] = relationship("RunbookExecution", back_populates="runbook")


class RunbookStep(Base):
    __tablename__ = "runbook_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_steps.id"), nullable=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "change" | "condition" | "human_checkpoint" | "parallel_group"
    type: Mapped[str] = mapped_column(String(32), nullable=False)

    # type="change"
    change_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    asset_selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # type="condition"
    condition_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    on_true_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_false_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # type="human_checkpoint"
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timeout_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_timeout: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "abort" | "continue"

    # Failure handling for "change" and "parallel_group"
    on_failure: Mapped[str] = mapped_column(String(32), nullable=False, default="abort")

    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="steps", foreign_keys=[runbook_id])
    children: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep",
        foreign_keys=[parent_step_id],
        order_by="RunbookStep.step_number",
    )


class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id"), nullable=False, index=True)
    runbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    runbook_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    triggered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # "running" | "waiting_human" | "completed" | "failed" | "rolled_back"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    step_results: Mapped[list["RunbookStepResult"]] = relationship(
        "RunbookStepResult",
        back_populates="execution",
        order_by="RunbookStepResult.step_number",
    )
    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="executions")


class RunbookStepResult(Base):
    __tablename__ = "runbook_step_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # "pending" | "running" | "waiting_human" | "completed" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    change_request_ids: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped["RunbookExecution"] = relationship("RunbookExecution", back_populates="step_results")
