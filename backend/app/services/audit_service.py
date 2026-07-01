# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent


async def record_event(
    db: AsyncSession,
    organization_id: uuid.UUID,
    event_type: str,
    event_payload: dict[str, Any],
    actor_id: uuid.UUID | None = None,
    change_request_id: uuid.UUID | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        actor_id=actor_id,
        change_request_id=change_request_id,
        event_type=event_type,
        event_payload=event_payload,
    )
    db.add(event)
    await db.flush()
    return event
