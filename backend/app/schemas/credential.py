import uuid
from datetime import datetime
from pydantic import BaseModel


class CredentialField(BaseModel):
    name: str
    label: str
    type: str  # "string" | "password"
    required: bool
    default: str | None = None


class CredentialRead(BaseModel):
    configured: bool
    fields: list[CredentialField]
    updated_at: datetime | None = None


class CredentialWrite(BaseModel):
    credentials: dict[str, str]


class AIProviderInfo(BaseModel):
    configured: bool


class AIProvidersRead(BaseModel):
    default: str | None
    providers: dict[str, AIProviderInfo]


class AIProviderWrite(BaseModel):
    api_key: str


class AIDefaultWrite(BaseModel):
    provider: str
