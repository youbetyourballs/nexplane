import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import Enum as SAEnum

from app.database import Base


class IdpType(str, enum.Enum):
    oidc = "oidc"
    ldap = "ldap"
    saml = "saml"


class IdpStatus(str, enum.Enum):
    pending = "pending"
    active = "active"


class IdentityProvider(Base):
    __tablename__ = "identity_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    type: Mapped[IdpType] = mapped_column(SAEnum(IdpType, name="idp_type", create_type=False), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[IdpStatus] = mapped_column(
        SAEnum(IdpStatus, name="idp_status", create_type=False), nullable=False, default=IdpStatus.pending
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
