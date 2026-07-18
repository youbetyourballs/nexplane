# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""CertificateInventory — tracks every cert issued by the platform for expiry monitoring."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class CertificateInventory(Base):
    __tablename__ = "certificate_inventory"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    subject = Column(String, nullable=False)
    san = Column(JSONB, nullable=False, server_default="[]")
    fingerprint = Column(String(64), nullable=False, unique=True)
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    change_request_id = Column(UUID(as_uuid=True), nullable=True)
