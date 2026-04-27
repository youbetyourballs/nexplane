import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ConnectorType(str, enum.Enum):
    aws_mock = "aws_mock"
    azure_mock = "azure_mock"
    cloudflare_mock = "cloudflare_mock"
    okta_mock = "okta_mock"
    paloalto_mock = "paloalto_mock"
    ssh_runner_mock = "ssh_runner_mock"
    active_directory_mock = "active_directory_mock"
    crowdstrike_mock = "crowdstrike_mock"
    tenable_mock = "tenable_mock"


class ConnectorStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    error = "error"


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    connector_type: Mapped[ConnectorType] = mapped_column(SAEnum(ConnectorType, name="connector_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ConnectorStatus] = mapped_column(SAEnum(ConnectorStatus, name="connector_status"), default=ConnectorStatus.active)
    scoped_permissions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship("Organization", back_populates="connectors")
