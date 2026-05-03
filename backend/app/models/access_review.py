"""Minimal AccessReview model stub used by the scheduler's access review creator.

A full implementation is expected in a future identity-lifecycle spec.
This stub satisfies the ImportError-guarded path in check_access_review_schedules.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AccessReview(Base):
    __tablename__ = "access_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="all_users")
    reviewer_rule: Mapped[str] = mapped_column(String(100), nullable=False, default="direct_manager")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
