"""Generic agent job dispatcher for nexplane_agent executors."""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone


async def dispatch_agent_job(
    command: str,
    parameters: dict,
    asset_ids: list,
    timeout_seconds: int = 300,
) -> dict:
    """
    Find the agent registered for the first asset_id, create a pending AgentJob,
    and wait for the agent to pick it up and return a result.

    Returns the job result dict on success.
    Raises RuntimeError if no agent is registered or the job times out.
    """
    from sqlalchemy import select, and_
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration, AgentJob, AgentJobStatus
    from app.models.asset import Asset
    import hmac as _hmac
    import hashlib
    import json

    if not asset_ids:
        raise RuntimeError("No asset_ids provided for agent dispatch")

    asset_id = uuid.UUID(asset_ids[0]) if isinstance(asset_ids[0], str) else asset_ids[0]

    async with AsyncSessionLocal() as db:
        # Find the asset and its organization
        asset = await db.get(Asset, asset_id)
        if not asset:
            raise RuntimeError(f"Asset {asset_id} not found")

        org_id = asset.organization_id

        # Find the most-recent AgentRegistration for this asset (multiple exist after re-deploys)
        reg_result = await db.execute(
            select(AgentRegistration).where(
                and_(
                    AgentRegistration.asset_id == asset_id,
                    AgentRegistration.organization_id == org_id,
                )
            ).order_by(AgentRegistration.last_seen.desc()).limit(1)
        )
        registration = reg_result.scalar_one_or_none()
        if not registration:
            raise RuntimeError(
                f"No agent registered for asset {asset_id}. Deploy the Nexplane agent first."
            )

        # Build HMAC signature
        from app.services.secrets_service import SecretsService
        from app import config as app_config
        svc = SecretsService(app_config.settings.SECRET_KEY)
        agent_secret_plain = ""
        from app.models.org_settings import OrganizationSettings
        org_settings = await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id == org_id
            )
        )
        org_settings = org_settings.scalar_one_or_none()
        if org_settings and org_settings.agent_secret_encrypted:
            agent_secret_plain = svc.decrypt(org_settings.agent_secret_encrypted)

        # Generate job_id up front so it can be included in the HMAC.
        # Go agent's agenthmac.Sign format: "{jobID}:{command}:{canonicalJSON(params)}"
        job_id = uuid.uuid4()
        canonical_params = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        message = f"{job_id}:{command}:{canonical_params}"
        sig = _hmac.new(
            agent_secret_plain.encode() if agent_secret_plain else b"",
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        # Create a pending AgentJob
        job = AgentJob(
            id=job_id,
            organization_id=org_id,
            agent_registration_id=registration.id,
            command=command,
            parameters=parameters,
            status=AgentJobStatus.pending,
            hmac_signature=sig,
            created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    # Poll for completion outside the session
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(10)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentJob).where(AgentJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if job and job.status not in (AgentJobStatus.pending, AgentJobStatus.running):
                if job.status == AgentJobStatus.completed:
                    return job.result or {"command": command, "status": "completed"}
                elif job.status == AgentJobStatus.failed:
                    err_msg = job.error or (job.result or {}).get("error") or "unknown error"
                    raise RuntimeError(f"Agent job {command} failed: {err_msg}")

    raise RuntimeError(
        f"Agent job {command} timed out after {timeout_seconds}s (job_id={job_id})"
    )
