# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, List
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification

_log = logging.getLogger(__name__)


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

    async def emit(self, event: NotificationEvent, cr: Any = None) -> None:
        """Emit a notification event.

        If *cr* is provided, routing rules are evaluated and may:
          - Add extra recipients (users expanded from rule user_ids / role_ids)
          - Suppress the default recipients when suppress_default=True

        External channel dispatch (Slack, email) is noted in the rule
        matched list; actual fan-out to those channels is a future
        integration point.
        """
        recipient_ids: list[str] = list(event.recipients)

        if cr is not None:
            try:
                from app.services.notification_routing_service import evaluate_routing_rules
                routing = await evaluate_routing_rules(self.db, cr, event.event_type)
                if routing["matched_rules"]:
                    _log.debug(
                        "Notification routing: matched rules %s for event %s",
                        routing["matched_rules"],
                        event.event_type,
                    )
                if routing["suppress_default"]:
                    recipient_ids = []
                # Merge routed user_ids (de-dup)
                for uid in routing["user_ids"]:
                    if uid not in recipient_ids:
                        recipient_ids.append(uid)
                # Log external channels — dispatch is a future integration
                for ch in routing["channels"]:
                    _log.info("Notification channel target (not yet dispatched): %s", ch)
            except Exception:
                _log.exception("Routing rule evaluation failed; falling back to default recipients")

        for recipient_id in recipient_ids:
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
