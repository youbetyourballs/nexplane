# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""SQLAlchemy model for per-dial tunnel audit records."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TunnelDialAudit(Base):
    __tablename__ = "tunnel_dial_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_registrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination_host: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_port: Mapped[int] = mapped_column(Integer(), nullable=False)
    bytes_sent: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    bytes_recv: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'normal', 'timeout', 'error', 'denied'
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
