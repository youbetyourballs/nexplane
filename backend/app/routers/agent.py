# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Response, WebSocket
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tunnel.relay import run_agent_tunnel
from app.models.agent import AgentRegistration, AgentJob, AgentJobStatus, OsType
from app.models.org_settings import OrganizationSettings
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.schemas.agent import (
    AgentRegisterRequest, AgentRegisterResponse,
    AgentJobResponse, AgentJobResultRequest,
)
from app.services.secrets_service import SecretsService
from app import config as app_config

router = APIRouter(prefix="/agent", tags=["Agent"])

_LONG_POLL_SECONDS = 30
_POLL_INTERVAL_SECONDS = 2
_TERMINAL_STATUSES = {AgentJobStatus.completed, AgentJobStatus.failed}


@router.websocket("/tunnel")
async def agent_tunnel(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """Reverse-tunnel: the agent dials out here; the control plane reaches
    allowlisted destinations in the agent's network through it. Auth + scoping
    handled in app.tunnel.relay."""
    await run_agent_tunnel(websocket, db)


def _secrets() -> SecretsService:
    return SecretsService(app_config.settings.SECRET_KEY)


async def _get_org_settings_by_secret(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettings:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    secrets = _secrets()
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.agent_secret_encrypted.is_not(None)
        )
    )
    for org_settings in result.scalars().all():
        try:
            if secrets.decrypt(org_settings.agent_secret_encrypted) == token:
                return org_settings
        except Exception:
            continue
    raise HTTPException(status_code=401, detail="Invalid agent secret")


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(AgentRegistration).where(
            and_(
                AgentRegistration.organization_id == org_id,
                AgentRegistration.machine_id == body.machine_id,
            )
        )
    )
    registration = result.scalar_one_or_none()

    if not registration:
        asset = Asset(
            organization_id=org_id,
            name=body.hostname,
            asset_type=AssetType.server,
            environment=Environment.prod,
            criticality=Criticality.medium,
            tags=["nexplane-agent", body.os_type.value],
            asset_metadata={
                "hostname": body.hostname,
                "os_type": body.os_type.value,
                "os_version": body.os_version,
                "agent_version": body.agent_version,
                "ip_addresses": body.ip_addresses,
                "last_seen": now.isoformat(),
            },
        )
        db.add(asset)
        await db.flush()

        registration = AgentRegistration(
            organization_id=org_id,
            machine_id=body.machine_id,
            asset_id=asset.id,
            hostname=body.hostname,
            os_type=body.os_type,
            ip_addresses=body.ip_addresses,
            os_version=body.os_version,
            agent_version=body.agent_version,
            last_seen=now,
        )
        db.add(registration)
        await db.flush()
    else:
        registration.hostname = body.hostname
        registration.ip_addresses = body.ip_addresses
        registration.os_version = body.os_version
        registration.agent_version = body.agent_version
        registration.last_seen = now

        if registration.asset_id:
            asset_result = await db.get(Asset, registration.asset_id)
            if asset_result:
                asset_result.name = body.hostname
                meta = dict(asset_result.asset_metadata or {})
                meta.update({
                    "hostname": body.hostname,
                    "os_type": body.os_type.value,
                    "os_version": body.os_version,
                    "agent_version": body.agent_version,
                    "ip_addresses": body.ip_addresses,
                    "last_seen": now.isoformat(),
                })
                asset_result.asset_metadata = meta

    await db.commit()
    await db.refresh(registration)

    return AgentRegisterResponse(
        agent_id=registration.id,
        asset_id=registration.asset_id,
        tunnel_enabled=bool(registration.tunnel_enabled),
        tunnel_allowlist=list(registration.tunnel_allowlist or []),
    )


@router.get("/jobs/next")
async def poll_next_job(
    agent_id: uuid.UUID,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id
    deadline = asyncio.get_event_loop().time() + _LONG_POLL_SECONDS

    reg_result = await db.execute(
        select(AgentRegistration).where(
            and_(
                AgentRegistration.id == agent_id,
                AgentRegistration.organization_id == org_id,
            )
        )
    )
    registration = reg_result.scalar_one_or_none()
    if registration:
        registration.last_seen = datetime.now(timezone.utc)
        await db.commit()

    while True:
        job_result = await db.execute(
            select(AgentJob).where(
                and_(
                    AgentJob.agent_registration_id == agent_id,
                    AgentJob.organization_id == org_id,
                    AgentJob.status == AgentJobStatus.pending,
                )
            ).order_by(AgentJob.created_at.asc()).limit(1)
        )
        job = job_result.scalar_one_or_none()

        if job:
            job.status = AgentJobStatus.running
            job.started_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(job)
            return AgentJobResponse(
                job_id=job.id,
                command=job.command,
                parameters=job.parameters,
                hmac_signature=job.hmac_signature,
            )

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return Response(status_code=204)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


@router.post("/jobs/{job_id}/result")
async def post_job_result(
    job_id: uuid.UUID,
    body: AgentJobResultRequest,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id
    job = await db.get(AgentJob, job_id)
    if not job or job.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Enforce agent ownership — only the assigned agent may submit results
    if job.agent_registration_id != body.agent_id:
        raise HTTPException(status_code=403, detail="Agent not authorized for this job")

    # Prevent duplicate completion of terminal jobs
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Job result already submitted")

    job.status = body.status
    job.result = body.result
    job.error = body.error
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/status/{asset_id}")
async def get_agent_status(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return the most-recent agent registration's last_seen for an asset.

    Used by smoke tests to verify the agent has actively reconnected
    to the backend after a re-deploy (last_seen within the last 60s).
    """
    result = await db.execute(
        select(AgentRegistration).where(
            AgentRegistration.asset_id == asset_id,
        ).order_by(AgentRegistration.last_seen.desc()).limit(1)
    )
    reg = result.scalar_one_or_none()
    if not reg:
        return {"registered": False, "last_seen": None, "seconds_ago": None}
    now = datetime.now(timezone.utc)
    last_seen = reg.last_seen
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    seconds_ago = (now - last_seen).total_seconds()
    return {
        "registered": True,
        "last_seen": last_seen.isoformat(),
        "seconds_ago": int(seconds_ago),
    }
