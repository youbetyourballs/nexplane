import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Text, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AccessReview(Base):
    __tablename__ = "access_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="collecting")
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decisions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])
