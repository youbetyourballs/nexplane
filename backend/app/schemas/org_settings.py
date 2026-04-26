from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    updated_at: datetime | None = None


class AIKeyUpdate(BaseModel):
    api_key: str
