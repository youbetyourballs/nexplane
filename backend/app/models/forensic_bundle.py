# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ForensicBundle(Base):
    __tablename__ = "forensic_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    upload_url: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped["Asset"] = relationship("Asset")
    change_request: Mapped["ChangeRequest | None"] = relationship("ChangeRequest")
