# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models.drift import DriftPolicy, DriftEvent, ResourceState
from app.models.user import User
from app.services.drift_service import (
    observe_surface,
    compute_diff,
    load_resource_state,
    normalize_state,
    create_shadow_cr,
    SURFACE_SEVERITY,
)

logger = logging.getLogger(__name__)


async def _resolve_policy_assets(db, policy: DriftPolicy) -> list[uuid.UUID]:
    """Return list of asset_ids covered by this policy."""
    if policy.scope_type == "asset":
        return [uuid.UUID(policy.scope_value)]
    # tag scope: find all assets with this tag
    from app.models.asset import Asset
    result = await db.execute(
        select(Asset.id).where(
            Asset.organization_id == policy.organization_id,
            Asset.tags.contains([policy.scope_value]),
        )
    )
    return [row[0] for row in result.fetchall()]


async def check_policy_drift() -> None:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(DriftPolicy).where(DriftPolicy.enabled == True)
        )
        policies = result.scalars().all()

        for policy in policies:
            # Respect per-policy cadence
            if policy.last_checked_at is not None:
                elapsed = (now - policy.last_checked_at).total_seconds()
                if elapsed < policy.poll_interval_seconds:
                    continue

            try:
                asset_ids = await _resolve_policy_assets(db, policy)
            except Exception as exc:
                logger.warning("drift worker: failed to resolve assets for policy %s: %s", policy.id, exc)
                continue

            for asset_id in asset_ids:
                for surface_type in policy.surface_types:
                    try:
                        await _check_one(db, policy, asset_id, surface_type, now)
                    except Exception as exc:
                        logger.warning(
                            "drift worker: check failed policy=%s asset=%s surface=%s: %s",
                            policy.id, asset_id, surface_type, exc,
                        )

            await db.execute(
                update(DriftPolicy)
                .where(DriftPolicy.id == policy.id)
                .values(last_checked_at=now)
            )

        await db.commit()


async def _check_one(db, policy: DriftPolicy, asset_id: uuid.UUID, surface_type: str, now: datetime) -> None:
    # Skip if attested and still within suppress window
    attested = await db.execute(
        select(DriftEvent).where(
            DriftEvent.organization_id == policy.organization_id,
            DriftEvent.asset_id == asset_id,
            DriftEvent.surface_type == surface_type,
            DriftEvent.status == "attested",
            DriftEvent.attested_suppress_until > now,
        )
    )
    if attested.scalar_one_or_none() is not None:
        return

    # Skip if open event already exists (dedup)
    open_event = await db.execute(
        select(DriftEvent).where(
            DriftEvent.organization_id == policy.organization_id,
            DriftEvent.asset_id == asset_id,
            DriftEvent.surface_type == surface_type,
            DriftEvent.status == "open",
        )
    )
    if open_event.scalar_one_or_none() is not None:
        return

    baseline = await load_resource_state(db, policy.organization_id, asset_id, surface_type)

    try:
        raw_observed = await observe_surface(db, policy.organization_id, asset_id, surface_type)
    except Exception as exc:
        logger.warning("drift worker: observation failed asset=%s surface=%s: %s", asset_id, surface_type, exc)
        return

    observed = normalize_state(surface_type, raw_observed)

    if baseline is None:
        # First observation — write anchor, no drift event
        from app.services.drift_service import upsert_resource_state
        await upsert_resource_state(
            db, policy.organization_id, asset_id, surface_type,
            observed, source="initial_observation",
        )
        return

    diff = compute_diff(baseline.state, observed)
    if not diff:
        return

    # Drift detected — create event + shadow CR
    from app.models.asset import Asset
    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = asset_result.scalar_one_or_none()
    asset_name = asset.name if asset else str(asset_id)

    event = DriftEvent(
        organization_id=policy.organization_id,
        asset_id=asset_id,
        surface_type=surface_type,
        drift_policy_id=policy.id,
        baseline_state=baseline.state,
        observed_state=observed,
        diff=diff,
        severity=SURFACE_SEVERITY.get(surface_type, "low"),
        detected_at=now,
        status="open",
    )
    db.add(event)
    await db.flush()  # get event.id

    user_id_result = await db.execute(
        select(User.id).where(User.organization_id == policy.organization_id).order_by(User.id).limit(1)
    )
    requester_id = user_id_result.scalar_one_or_none()
    if requester_id is None:
        logger.warning("No users found in org %s; skipping shadow CR", policy.organization_id)
    else:
        shadow_cr_id = await create_shadow_cr(db, event, asset_name, requester_id=requester_id)
        event.shadow_cr_id = shadow_cr_id

    logger.info(
        "drift: event created org=%s asset=%s surface=%s severity=%s shadow_cr=%s",
        policy.organization_id, asset_id, surface_type, event.severity, shadow_cr_id,
    )
