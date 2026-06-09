import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel
from app.models.identity_provider import IdpType, IdpStatus


class IdentityProviderCreate(BaseModel):
    type: IdpType
    name: str
    config: dict[str, Any]
    connector_id: uuid.UUID | None = None


class IdentityProviderUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    connector_id: uuid.UUID | None = None


class IdentityProviderRead(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    type: IdpType
    name: str
    status: IdpStatus
    enabled: bool
    config: dict[str, Any]
    connector_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuthModeSwitch(BaseModel):
    auth_mode: str
    idp_id: uuid.UUID | None = None
