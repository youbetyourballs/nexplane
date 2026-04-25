import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.connector import ConnectorType, ConnectorStatus


class ConnectorCreate(BaseModel):
    connector_type: ConnectorType
    name: str
    scoped_permissions: dict = {}


class ConnectorRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    connector_type: ConnectorType
    name: str
    status: ConnectorStatus
    scoped_permissions: dict
    created_at: datetime


class ConnectorTestResult(BaseModel):
    success: bool
    latency_ms: int
    message: str
    details: dict = {}
