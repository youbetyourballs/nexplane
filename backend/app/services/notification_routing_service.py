# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_routing_rule import NotificationRoutingRule
from app.models.user import User

_log = logging.getLogger(__name__)


def _rule_matches(rule: NotificationRoutingRule, cr: Any) -> bool:
    """Return True when all non-null conditions on the rule match the CR.

    cr is expected to expose:
      .change_type  (str or ChangeType enum)
      .severity     (str, nullable)
      .asset_tags   (dict, nullable)
      .connector_type (str, nullable)
      .status       (str or ChangeRequestStatus enum)
    """
    # match_cr_types
    if rule.match_cr_types:
        cr_type = str(getattr(cr, "change_type", "") or "")
        if cr_type not in rule.match_cr_types:
            return False

    # match_severities
    if rule.match_severities:
        severity = str(getattr(cr, "severity", "") or "")
        if severity not in rule.match_severities:
            return False

    # match_asset_tags — every key/value in the rule must be present in the CR
    if rule.match_asset_tags:
        cr_tags: dict = getattr(cr, "asset_tags", None) or {}
        for k, v in rule.match_asset_tags.items():
            if cr_tags.get(k) != v:
                return False

    # match_connector_types
    if rule.match_connector_types:
        connector_type = str(getattr(cr, "connector_type", "") or "")
        if connector_type not in rule.match_connector_types:
            return False

    # match_status
    if rule.match_status:
        status = str(getattr(cr, "status", "") or "")
        if status not in rule.match_status:
            return False

    return True


async def evaluate_routing_rules(
    db: AsyncSession,
    cr: Any,
    event_type: str,
) -> dict:
    """Evaluate all enabled routing rules against the given CR.

    Returns a dict:
      {
        "user_ids": list[str],   # de-duplicated
        "channels": list[str],
        "suppress_default": bool,
        "matched_rules": list[str],  # rule names that fired
      }
    """
    result = await db.execute(
        select(NotificationRoutingRule)
        .where(NotificationRoutingRule.enabled.is_(True))
        .order_by(NotificationRoutingRule.priority.asc())
    )
    rules: list[NotificationRoutingRule] = list(result.scalars().all())

    user_ids: set[str] = set()
    channels: list[str] = []
    suppress_default = False
    matched: list[str] = []

    for rule in rules:
        if not _rule_matches(rule, cr):
            continue

        matched.append(rule.name)

        if rule.notify_user_ids:
            user_ids.update(rule.notify_user_ids)

        if rule.notify_role_ids:
            # Expand roles to user IDs within the same org
            org_id = getattr(cr, "organization_id", None)
            if org_id:
                role_result = await db.execute(
                    select(User.id).where(
                        User.organization_id == org_id,
                        User.role.in_(rule.notify_role_ids),
                    )
                )
                for (uid,) in role_result.fetchall():
                    user_ids.add(str(uid))

        if rule.notify_channels:
            for ch in rule.notify_channels:
                if ch not in channels:
                    channels.append(ch)

        if rule.suppress_default:
            suppress_default = True

    return {
        "user_ids": list(user_ids),
        "channels": channels,
        "suppress_default": suppress_default,
        "matched_rules": matched,
    }


async def preview_routing_rules(
    db: AsyncSession,
    cr_payload: dict,
) -> dict:
    """Dry-run: evaluate rules against a synthetic CR payload dict.

    Accepts a plain dict so callers don't need a real ORM object.
    Returns the same structure as evaluate_routing_rules.
    """

    class _FakeCR:
        pass

    fake = _FakeCR()
    for k, v in cr_payload.items():
        setattr(fake, k, v)

    return await evaluate_routing_rules(db, fake, cr_payload.get("event_type", ""))
