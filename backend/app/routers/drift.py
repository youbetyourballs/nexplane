# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/routers/drift.py — initial file, expanded in Task 8

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/drift", tags=["drift"])
logger = logging.getLogger(__name__)


async def _verify_agent_hmac(request: Request, db: AsyncSession) -> str:
    """Verify HMAC-SHA256 signature from agent. Returns asset_id on success."""
    from sqlalchemy import select
    from app.models.org_settings import OrganizationSettings
    from app.services.secrets_service import SecretsService
    from app import config as app_config

    signature = request.headers.get("X-Nexplane-Signature")
    asset_id = request.headers.get("X-Nexplane-Asset-Id")
    org_id = request.headers.get("X-Nexplane-Org-Id")

    if not all([signature, asset_id, org_id]):
        raise HTTPException(status_code=401, detail="Missing HMAC headers")

    try:
        org_uuid = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid org_id")

    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == org_uuid
        )
    )
    settings = result.scalar_one_or_none()
    if settings is None or not settings.agent_secret_encrypted:
        raise HTTPException(status_code=401, detail="No agent secret configured")

    secrets_svc = SecretsService(app_config.settings.SECRET_KEY)
    secret = secrets_svc.decrypt(settings.agent_secret_encrypted).encode()
    body = await request.body()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    return asset_id


@router.post("/agent-event")
async def receive_agent_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Tripwire endpoint: agents POST here when they detect a change on a monitored
    surface. The platform verifies the HMAC, runs an immediate observation, diffs
    against the baseline, and creates a shadow CR if drift is found.
    """
    asset_id_str = await _verify_agent_hmac(request, db)
    body = await request.json()

    surface_type = body.get("surface_type")
    if not surface_type:
        raise HTTPException(status_code=422, detail="surface_type required")

    from sqlalchemy import select
    from app.models.asset import Asset
    from app.models.drift import DriftPolicy, DriftEvent
    from app.models.user import User
    from app.services.drift_service import (
        observe_surface,
        compute_diff,
        load_resource_state,
        normalize_state,
        SURFACE_SEVERITY,
        create_shadow_cr,
    )

    asset_id = uuid.UUID(asset_id_str)

    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    org_id = asset.organization_id

    try:
        raw_observed = await observe_surface(db, org_id, asset_id, surface_type)
        observed = normalize_state(surface_type, raw_observed)
        baseline = await load_resource_state(db, org_id, asset_id, surface_type)

        if baseline:
            diff = compute_diff(baseline.state, observed)
            if diff:
                policy_result = await db.execute(
                    select(DriftPolicy).where(
                        DriftPolicy.organization_id == org_id,
                        DriftPolicy.scope_value == str(asset_id),
                        DriftPolicy.scope_type == "asset",
                    )
                )
                policy = policy_result.scalars().first()
                if policy:
                    event = DriftEvent(
                        organization_id=org_id,
                        asset_id=asset_id,
                        surface_type=surface_type,
                        drift_policy_id=policy.id,
                        baseline_state=baseline.state,
                        observed_state=observed,
                        diff=diff,
                        severity=SURFACE_SEVERITY.get(surface_type, "low"),
                        detected_at=datetime.now(timezone.utc),
                        status="open",
                    )
                    db.add(event)
                    await db.flush()

                    # Query a real user from the org for shadow CR requester_id
                    user_result = await db.execute(
                        select(User.id).where(User.organization_id == org_id).order_by(User.id).limit(1)
                    )
                    requester_id = user_result.scalar_one_or_none()

                    if requester_id is None:
                        logger.warning("No users found in org %s; skipping shadow CR creation", org_id)
                        await db.commit()
                        return {"received": True, "drift_event_id": str(event.id)}

                    shadow_cr_id = await create_shadow_cr(
                        db, event, asset.name, requester_id=requester_id
                    )
                    event.shadow_cr_id = shadow_cr_id
                    await db.commit()
                    return {"received": True, "drift_event_id": str(event.id)}

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "agent-event: observation failed asset=%s surface=%s: %s",
            asset_id, surface_type, exc,
        )

    return {"received": True, "drift_event_id": None}
