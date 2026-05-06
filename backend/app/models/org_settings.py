import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, func, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True
    )
    anthropic_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_providers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    sla_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
