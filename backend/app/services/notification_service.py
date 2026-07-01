# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification


@dataclass
class NotificationEvent:
    event_type: str
    organization_id: str
    message: str
    recipients: List[str]
    actor_id: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def emit(self, event: NotificationEvent) -> None:
        for recipient_id in event.recipients:
            n = Notification(
                organization_id=uuid.UUID(event.organization_id),
                recipient_user_id=uuid.UUID(recipient_id),
                event_type=event.event_type,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                message=event.message,
            )
            self.db.add(n)
        await self.db.commit()
