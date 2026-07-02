# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class AssetDependency(Base):
    """Typed dependency edge between two assets.

    ``dependent_asset_id`` depends on ``dependency_asset_id``.
    Examples:
      - app-service  --[uses_cert]-->   wildcard.acme.internal
      - prod-worker  --[hosted_on]-->   prod-hypervisor-01
      - prod-api     --[connects_to]--> prod-postgres-01
      - prod-gateway --[listens_on_port 8443]--> (self, recorded in dep_metadata)
    """

    __tablename__ = "asset_dependencies"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "dependent_asset_id",
            "dependency_asset_id",
            "dependency_type",
            name="uq_asset_dependencies_org_dep_type",
        ),
        Index("ix_asset_dependencies_org_dependent", "organization_id", "dependent_asset_id"),
        Index("ix_asset_dependencies_org_dependency", "organization_id", "dependency_asset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The asset consuming the dependency
    dependent_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The asset providing the dependency
    dependency_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Relationship vocabulary: uses_cert, connects_to, hosted_on, listens_on_port, etc.
    dependency_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Arbitrary metadata — port numbers, CIDR ranges, protocol, etc.
    dep_metadata: Mapped[dict] = mapped_column(JSONB(), nullable=False, default=dict)
    # How this record was created
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
