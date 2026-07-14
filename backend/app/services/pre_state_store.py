# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org_settings import OrganizationSettings
from app.models.pre_state_snapshot import PreStateSnapshot

_DEFAULT_RETENTION_DAYS = 30


class PreStateStore:
    @staticmethod
    async def capture(
        db: AsyncSession,
        cr_id: _uuid.UUID,
        step_id: str,
        org_id: _uuid.UUID,
        state: dict,
    ) -> None:
        retention_days = _DEFAULT_RETENTION_DAYS
        result = await db.execute(
            select(OrganizationSettings).where(OrganizationSettings.organization_id == org_id)
        )
        org_settings = result.scalar_one_or_none()
        if org_settings is not None:
            retention_days = org_settings.pre_state_retention_days

        now = datetime.now(timezone.utc)
        snapshot = PreStateSnapshot(
            id=_uuid.uuid4(),
            cr_id=cr_id,
            step_id=step_id,
            organization_id=org_id,
            state_json=state,
            captured_at=now,
            expires_at=now + timedelta(days=retention_days),
        )
        db.add(snapshot)
        await db.flush()

    @staticmethod
    async def retrieve(
        db: AsyncSession,
        cr_id: _uuid.UUID,
        step_id: str,
        org_id: _uuid.UUID,
    ) -> dict | None:
        result = await db.execute(
            select(PreStateSnapshot).where(
                PreStateSnapshot.cr_id == cr_id,
                PreStateSnapshot.step_id == step_id,
                PreStateSnapshot.organization_id == org_id,
                PreStateSnapshot.expires_at > datetime.now(timezone.utc),
            )
        )
        snapshot = result.scalar_one_or_none()
        if snapshot is None:
            return None
        return snapshot.state_json

    @staticmethod
    async def purge_expired(db: AsyncSession) -> int:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(PreStateSnapshot).where(PreStateSnapshot.expires_at < now)
        )
        return result.rowcount
