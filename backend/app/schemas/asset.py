import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.asset import AssetType, Environment, Criticality


class AssetCreate(BaseModel):
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    metadata: dict = {}


class AssetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    metadata: dict
    created_at: datetime
