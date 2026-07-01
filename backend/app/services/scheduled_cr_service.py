# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus


async def schedule_reversal_cr(
    original_parameters: dict,
    original_asset_ids: list,
    execute_after_hours: float,
    change_type: str,
    organization_id: str | None = None,
) -> str:
    execute_at = datetime.now(timezone.utc) + timedelta(hours=execute_after_hours)
    async with AsyncSessionLocal() as db:
        cr = ChangeRequest(
            id=uuid.uuid4(),
            title=f"Auto-reversal: {change_type} at {execute_at.strftime('%Y-%m-%d %H:%M UTC')}",
            change_type=change_type,
            target_asset_ids=[str(a) for a in original_asset_ids],
            desired_outcome={**original_parameters, "_auto_reversal": True},
            execute_at=execute_at,
            status=ChangeRequestStatus.approved,
        )
        if organization_id:
            cr.organization_id = uuid.UUID(organization_id)
        db.add(cr)
        await db.commit()
        return str(cr.id)
