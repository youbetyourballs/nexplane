import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AccessReviewSchedule(Base):
    __tablename__ = "access_review_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    frequency_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # "all_users" | "by_tag:<tag_name>"
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="all_users")
    # "direct_manager" | "security_team" | "asset_owner"
    reviewer_assignment_rule: Mapped[str] = mapped_column(String(100), nullable=False, default="direct_manager")
    last_review_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
