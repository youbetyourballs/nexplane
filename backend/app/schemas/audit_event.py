# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from pydantic import BaseModel


class AuditEventRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    actor_id: uuid.UUID | None
    change_request_id: uuid.UUID | None
    event_type: str
    event_payload: dict
    created_at: datetime
