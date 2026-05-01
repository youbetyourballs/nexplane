import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Text, Enum as SAEnum, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OsType(str, enum.Enum):
    linux = "linux"
    windows = "windows"


class AgentJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentRegistration(Base):
    __tablename__ = "agent_registrations"
    __table_args__ = (
        UniqueConstraint("organization_id", "machine_id", name="uq_agent_registrations_org_machine"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    os_type: Mapped[OsType] = mapped_column(SAEnum(OsType, name="os_type"), nullable=False)
    ip_addresses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    os_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentJob(Base):
    __tablename__ = "agent_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    agent_registration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_registrations.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    command: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    hmac_signature: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[AgentJobStatus] = mapped_column(
        SAEnum(AgentJobStatus, name="agent_job_status"),
        nullable=False,
        default=AgentJobStatus.pending,
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
