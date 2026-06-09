import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrgAuthMode(str, enum.Enum):
    local = "local"
    idp = "idp"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    auth_mode: Mapped[OrgAuthMode] = mapped_column(
        SAEnum(OrgAuthMode, name="org_auth_mode", create_type=False),
        nullable=False,
        default=OrgAuthMode.local,
        server_default="local",
    )
    auth_mode_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    users: Mapped[list["User"]] = relationship("User", back_populates="organization")
    assets: Mapped[list["Asset"]] = relationship("Asset", back_populates="organization")
    connectors: Mapped[list["Connector"]] = relationship("Connector", back_populates="organization")
    change_requests: Mapped[list["ChangeRequest"]] = relationship("ChangeRequest", back_populates="organization")
    audit_events: Mapped[list["AuditEvent"]] = relationship("AuditEvent", back_populates="organization")
