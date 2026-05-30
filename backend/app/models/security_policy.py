# backend/app/models/security_policy.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from app.database import Base


class SoakSessionStatus(str, enum.Enum):
    running = "running"
    stopped = "stopped"
    synthesized = "synthesized"
    cr_proposed = "cr_proposed"


class PolicyType(str, enum.Enum):
    seccomp = "seccomp"
    apparmor = "apparmor"
    selinux = "selinux"
    network_policy = "network_policy"


class SecurityPolicySoakSession(Base):
    __tablename__ = "security_policy_soak_sessions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False, default="seccomp")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    asset_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    raw_observations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synthesized_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    baseline_delta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityPolicyBaseline(Base):
    __tablename__ = "security_policy_baselines"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False, default="seccomp")
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
