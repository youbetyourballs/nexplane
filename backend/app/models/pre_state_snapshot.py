# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from app.database import Base


class PreStateSnapshot(Base):
    __tablename__ = "pre_state_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cr_id = Column(UUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    step_id = Column(String, nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    state_json = Column(JSON, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_pre_state_snapshots_cr_id", "cr_id"),
        Index("ix_pre_state_snapshots_org_id", "organization_id"),
        Index("ix_pre_state_snapshots_expires_at", "expires_at"),
    )
