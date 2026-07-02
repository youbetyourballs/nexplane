# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationRoutingRule(Base):
    __tablename__ = "notification_routing_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Conditions — all non-null fields must match (AND logic); null = any
    match_cr_types: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    match_severities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    match_asset_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    match_connector_types: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    match_status: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Actions
    notify_user_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    notify_role_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    notify_channels: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    suppress_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
