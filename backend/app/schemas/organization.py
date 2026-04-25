import uuid
from datetime import datetime
from pydantic import BaseModel


class OrganizationRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    created_at: datetime
