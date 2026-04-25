import uuid
from datetime import datetime
from sqlalchemy import DateTime, func, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class PlanGeneratedBy(str, enum.Enum):
    system = "system"
    ai_mock = "ai_mock"
    human = "human"


class ChangePlan(Base):
    __tablename__ = "change_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False, unique=True
    )
    generated_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    preflight_checks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    blast_radius: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rollback_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verification_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_by: Mapped[PlanGeneratedBy] = mapped_column(
        SAEnum(PlanGeneratedBy, name="plan_generated_by"), default=PlanGeneratedBy.system
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    change_request: Mapped["ChangeRequest"] = relationship("ChangeRequest", back_populates="change_plan")
