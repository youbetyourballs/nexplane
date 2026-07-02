# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import enum
import uuid
import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CriteriaType(str, enum.Enum):
    cr_completed = "cr_completed"
    host_state_check = "host_state_check"
    service_check = "service_check"
    port_check = "port_check"
    manual = "manual"


class CriteriaResult(str, enum.Enum):
    pass_ = "pass"
    fail = "fail"
    pending_manual = "pending_manual"
    not_checked = "not_checked"


class ProjectSuccessCriteria(Base):
    __tablename__ = "project_success_criteria"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[CriteriaType] = mapped_column(
        SAEnum(CriteriaType, name="criteriatype"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    assertion: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_checked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_result: Mapped[CriteriaResult] = mapped_column(
        SAEnum(CriteriaResult, name="criteriaresult"),
        nullable=False,
        default=CriteriaResult.not_checked,
    )
    last_result_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.datetime.utcnow,
    )

    project: Mapped["Project"] = relationship(  # type: ignore[name-defined]
        "Project", back_populates="success_criteria"
    )

    __table_args__ = (
        Index("ix_project_success_criteria_project_id", "project_id"),
    )
