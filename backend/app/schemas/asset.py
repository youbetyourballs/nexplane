import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, field_validator
from app.models.asset import AssetType, Environment, Criticality


class AssetCreate(BaseModel):
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    asset_metadata: dict = {}
    tags: list[str] = []


class AssetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    connector_id: Optional[uuid.UUID] = None
    connector_name: Optional[str] = None
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    asset_metadata: dict
    tags: list[str]
    created_at: datetime


class AssetUpdate(BaseModel):
    name: str | None = None
    criticality: Criticality | None = None
    asset_metadata: dict | None = None
    tags: list[str] | None = None


class BulkTagOperation(BaseModel):
    asset_ids: list[uuid.UUID]
    operation: Literal["add", "remove", "set"]
    tags: list[str]

    @field_validator("tags")
    @classmethod
    def tags_not_empty(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("tags must contain at least one entry")
        return v
