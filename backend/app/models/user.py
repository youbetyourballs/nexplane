import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    security_operator = "security_operator"
    approver = "approver"
    auditor = "auditor"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, name="user_role"), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")
    change_requests: Mapped[list["ChangeRequest"]] = relationship("ChangeRequest", back_populates="requester")
    approvals: Mapped[list["Approval"]] = relationship("Approval", back_populates="approver")
    audit_events: Mapped[list["AuditEvent"]] = relationship("AuditEvent", back_populates="actor")
