import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ConnectorType(str, enum.Enum):
    aws = "aws"
    azure = "azure"
    cloudflare = "cloudflare"
    okta = "okta"
    paloalto = "paloalto"
    ssh = "ssh"
    active_directory = "active_directory"
    crowdstrike = "crowdstrike"
    tenable = "tenable"
    nexplane_agent = "nexplane_agent"
    gcp = "gcp"
    runzero = "runzero"
    wiz = "wiz"
    entra_id = "entra_id"


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
