from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    agent_configured: bool = False
    updated_at: datetime | None = None
    agent_secret_plaintext: str | None = None


class AIKeyUpdate(BaseModel):
    api_key: str


class AgentSecretUpdate(BaseModel):
    secret: str
