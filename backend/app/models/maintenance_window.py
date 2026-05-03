import uuid
from sqlalchemy import Column, Integer, String, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_window"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name            = Column(String(255), nullable=False)
    # cron expression, e.g. "0 2 * * 6" = Saturdays at 02:00 UTC
    cron_schedule   = Column(String(100), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    # null = applies to all assets; otherwise list of tag name strings
    applies_to_tags = Column(JSON, nullable=True)
    enabled         = Column(Boolean, nullable=False, default=True)
