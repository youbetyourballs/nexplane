# backend/app/models/backup_target.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Integer, ForeignKey, Enum as SAEnum, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.database import Base


class BackupTargetStatus(str, enum.Enum):
    healthy = "healthy"
    overdue = "overdue"
    unprotected = "unprotected"


class BackupTarget(Base):
    __tablename__ = "backup_targets"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recurring_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("recurring_jobs.id", ondelete="SET NULL"), nullable=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    target_description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_cadence_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    last_successful_backup_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    last_successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[BackupTargetStatus] = mapped_column(
        SAEnum(BackupTargetStatus, name="backup_target_status", native_enum=False), nullable=False, default=BackupTargetStatus.unprotected
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
