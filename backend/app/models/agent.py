# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Text, Enum as SAEnum, JSON, UniqueConstraint, Boolean, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OsType(str, enum.Enum):
    linux = "linux"
    windows = "windows"
    darwin = "darwin"


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
    # Reverse-tunnel connectivity (opt-in, off by default). tunnel_allowlist is a
    # list of "CIDR|IP|hostname:port[-port|*]" strings; deny-by-default.
    tunnel_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    tunnel_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    tunnel_max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")


class TunnelConnectionToken(Base):
    """Short-lived, single-use token issued immediately before a tunnel WS connection.

    The agent calls POST /agents/{agent_id}/tunnel-token (authenticated with its
    long-lived HMAC secret) to obtain a 32-byte random token valid for 60 seconds.
    That token is used *once* for the WS handshake; the relay marks it used_at on
    acceptance and rejects any reuse or expired token.  A stolen WS bearer token
    therefore cannot be replayed — it is already consumed after the handshake.
    """
    __tablename__ = "tunnel_connection_tokens"
    __table_args__ = (
        Index("ix_tunnel_connection_tokens_token_hash", "token_hash"),
        Index("ix_tunnel_connection_tokens_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_registrations.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
