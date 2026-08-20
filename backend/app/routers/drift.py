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


# ── Additional imports for policy/event endpoints ───────────────────────────

from typing import Optional
from app.schemas.drift import (
    DriftPolicyCreate, DriftPolicyRead, DriftPolicyUpdate,
    DriftEventRead, DriftEventAcceptBody, DriftEventAttestBody,
    ResourceStateRead, AssetDriftSummary,
)
from app.models.drift import ResourceState, DriftPolicy, DriftEvent
from sqlalchemy import select, update


# ── Drift Policies ──────────────────────────────────────────────────────────

@router.get("/policies", response_model=list[DriftPolicyRead])
async def list_drift_policies(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DriftPolicy).order_by(DriftPolicy.created_at.desc()))
    return result.scalars().all()


@router.post("/policies", response_model=DriftPolicyRead)
async def create_drift_policy(
    body: DriftPolicyCreate,
    db: AsyncSession = Depends(get_db),
):
    policy = DriftPolicy(
        **body.model_dump(),
        auto_created=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    # Immediately run initial observation to establish anchor
    from app.services.drift_service import observe_surface, upsert_resource_state
    for surface_type in policy.surface_types:
        try:
            asset_id = uuid.UUID(policy.scope_value)
            observed = await observe_surface(db, policy.organization_id, asset_id, surface_type)
            await upsert_resource_state(
                db, policy.organization_id, asset_id, surface_type,
                observed, source="initial_observation",
            )
        except Exception as exc:
            logger.warning("create_drift_policy: initial observation failed surface=%s: %s", surface_type, exc)

    return policy


@router.get("/policies/{policy_id}", response_model=DriftPolicyRead)
async def get_drift_policy(policy_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftPolicy).where(DriftPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="DriftPolicy not found")
    return policy


@router.patch("/policies/{policy_id}", response_model=DriftPolicyRead)
async def update_drift_policy(
    policy_id: uuid.UUID,
    body: DriftPolicyUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DriftPolicy).where(DriftPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="DriftPolicy not found")
    update_data = body.model_dump(exclude_none=True)
    for k, v in update_data.items():
        setattr(policy, k, v)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_drift_policy(policy_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftPolicy).where(DriftPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="DriftPolicy not found")
    if policy.auto_created:
        raise HTTPException(status_code=409, detail="Auto-created policies cannot be deleted")
    await db.delete(policy)
    await db.commit()


# ── Drift Events ────────────────────────────────────────────────────────────

@router.get("/events", response_model=list[DriftEventRead])
async def list_drift_events(
    status: Optional[str] = None,
    asset_id: Optional[uuid.UUID] = None,
    surface_type: Optional[str] = None,
    severity: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(DriftEvent).order_by(DriftEvent.detected_at.desc())
    if status:
        query = query.where(DriftEvent.status == status)
    if asset_id:
        query = query.where(DriftEvent.asset_id == asset_id)
    if surface_type:
        query = query.where(DriftEvent.surface_type == surface_type)
    if severity:
        query = query.where(DriftEvent.severity == severity)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/events/{event_id}", response_model=DriftEventRead)
async def get_drift_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftEvent).where(DriftEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="DriftEvent not found")
    return event


@router.post("/events/{event_id}/accept", response_model=DriftEventRead)
async def accept_drift_event(
    event_id: uuid.UUID,
    body: DriftEventAcceptBody,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DriftEvent).where(DriftEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="DriftEvent not found")
    if event.status != "open":
        raise HTTPException(status_code=409, detail=f"Event is already {event.status}")

    from app.services.drift_service import upsert_resource_state
    now = datetime.now(timezone.utc)

    await upsert_resource_state(
        db=db,
        org_id=event.organization_id,
        asset_id=event.asset_id,
        surface_type=event.surface_type,
        state=event.observed_state,
        source="accepted",
        accepted_at=now,
        acceptance_note=body.note,
    )

    if event.shadow_cr_id:
        from app.models.change_request import ChangeRequest, ChangeRequestStatus
        await db.execute(
            update(ChangeRequest)
            .where(ChangeRequest.id == event.shadow_cr_id)
            .values(status=ChangeRequestStatus.cancelled)
        )

    event.status = "accepted"
    event.resolved_at = now
    event.resolution_note = body.note
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/events/{event_id}/attest", response_model=DriftEventRead)
async def attest_drift_event(
    event_id: uuid.UUID,
    body: DriftEventAttestBody,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DriftEvent).where(DriftEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="DriftEvent not found")
    if event.status != "open":
        raise HTTPException(status_code=409, detail=f"Event is already {event.status}")

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    event.status = "attested"
    event.resolved_at = now
    event.resolution_note = body.note
    event.attested_suppress_until = now + timedelta(days=body.snooze_days)
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/events/{event_id}/dismiss", response_model=DriftEventRead)
async def dismiss_drift_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftEvent).where(DriftEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="DriftEvent not found")
    if event.status != "open":
        raise HTTPException(status_code=409, detail=f"Event is already {event.status}")

    event.status = "dismissed"
    event.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(event)
    return event


# ── Manual Drift Check Trigger ──────────────────────────────────────────────

from pydantic import BaseModel


class DriftCheckRequest(BaseModel):
    asset_id: uuid.UUID
    surface_type: str


@router.post("/check")
async def manual_drift_check(
    body: DriftCheckRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate drift check for a specific asset + surface. Used by smoke tests."""
    from sqlalchemy import select
    from app.models.drift import DriftPolicy
    from app.workers.drift_check_worker import _check_one

    policy_result = await db.execute(
        select(DriftPolicy).where(
            DriftPolicy.scope_value == str(body.asset_id),
            DriftPolicy.scope_type == "asset",
            DriftPolicy.surface_types.contains([body.surface_type]),
        )
    )
    policy = policy_result.scalars().first()
    if policy is None:
        raise HTTPException(status_code=404, detail="No DriftPolicy found for this asset + surface_type")

    await _check_one(db, policy, body.asset_id, body.surface_type, datetime.now(timezone.utc))
    await db.commit()
    return {"checked": True}
