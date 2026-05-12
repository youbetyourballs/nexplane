import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
import sqlalchemy as sa
from app.database import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    paused = "paused"
    complete = "complete"
    failed = "failed"
    aborted = "aborted"


class PatchCampaign(Base):
    __tablename__ = "patch_campaigns"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    cve_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    health_gate_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    health_endpoint: Mapped[str] = mapped_column(String(500), nullable=False, default="/health")
    abort_threshold: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.2)
    rollout_strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="rolling")
    status: Mapped[CampaignStatus] = mapped_column(sa.Enum(CampaignStatus, name="campaign_status"), nullable=False, default=CampaignStatus.draft)
    batches: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
