# backend/app/services/security_policy/soak_service.py
"""Orchestrates soak session lifecycle: observation dispatch, synthesis, CR creation."""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.executors.nexplane_agent import _dispatch
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline
from app.services.security_policy.synthesizer import compute_delta, synthesize_seccomp


async def _collect_observations(
    asset_ids: list[str],
    window_seconds: int,
) -> tuple[dict[str, list[str]], bool]:
    """Run seccomp_learn on each asset concurrently. Returns (observations, partial)."""
    partial = False

    async def _observe_one(asset_id: str) -> tuple[str, list[str] | None]:
        try:
            result = await _dispatch.dispatch_agent_job(
                command="seccomp_learn",
                parameters={"duration_seconds": window_seconds},
                asset_ids=[asset_id],
                timeout_seconds=window_seconds + 60,
            )
            return asset_id, result.get("syscalls_seen", [])
        except Exception:
            return asset_id, None

    results = await asyncio.gather(*[_observe_one(aid) for aid in asset_ids])
    observations: dict[str, list[str]] = {}
    for asset_id, syscalls in results:
        if syscalls is None:
            partial = True
        else:
            observations[asset_id] = syscalls
    return observations, partial


def _build_cr_params(session: SecurityPolicySoakSession, profile: dict, service_name: str) -> dict:
    return {
        "session_id": str(session.id),
        "service_name": service_name,
        "profile": json.dumps(profile),
    }


def should_auto_propose(baseline: SecurityPolicyBaseline | None) -> bool:
    return baseline is None


async def start_session(
    db: AsyncSession,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    policy_type: str,
    asset_ids: list[str],
    window_seconds: int,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    session = SecurityPolicySoakSession(
        organization_id=organization_id,
        project_id=project_id,
        policy_type=policy_type,
        asset_ids=asset_ids,
        window_seconds=window_seconds,
        status="running",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def stop_and_synthesize(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    service_name: str,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    """Collect observations, synthesize profile, compare baseline, create CR if auto-propose."""
    observations, partial = await _collect_observations(
        asset_ids=[str(a) for a in session.asset_ids],
        window_seconds=session.window_seconds,
    )

    profile = synthesize_seccomp(observations)

    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.organization_id == session.organization_id,
            SecurityPolicyBaseline.project_id == session.project_id,
            SecurityPolicyBaseline.policy_type == session.policy_type,
        )
    )
    baseline = result.scalar_one_or_none()

    delta = None
    if baseline:
        delta = compute_delta(baseline.profile, profile)

    session.raw_observations = observations
    session.synthesized_profile = profile
    session.baseline_delta = delta
    session.partial = partial
    session.stopped_at = datetime.now(timezone.utc)
    session.status = "synthesized"

    if should_auto_propose(baseline):
        cr = await _create_configure_seccomp_cr(db, session, profile, service_name, user_id)
        await _upsert_baseline(db, session, profile, cr.id)
        session.cr_id = cr.id
        session.status = "cr_proposed"

    await db.commit()
    await db.refresh(session)
    return session


async def accept_diff(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    service_name: str,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    """Operator accepted the diff — create CR and update baseline."""
    if session.status not in ("synthesized",):
        raise ValueError(f"Session status must be 'synthesized', got {session.status!r}")
    if session.synthesized_profile is None:
        raise ValueError("Session has no synthesized profile")
    if session.baseline_delta is None:
        raise ValueError("No baseline delta to accept — use stop response cr_id for first-run sessions")

    cr = await _create_configure_seccomp_cr(
        db, session, session.synthesized_profile, service_name, user_id
    )
    await _upsert_baseline(db, session, session.synthesized_profile, cr.id)
    session.cr_id = cr.id
    session.status = "cr_proposed"
    await db.commit()
    await db.refresh(session)
    return session


async def _create_configure_seccomp_cr(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    service_name: str,
    user_id: uuid.UUID,
) -> ChangeRequest:
    params = _build_cr_params(session, profile, service_name)
    cr = ChangeRequest(
        organization_id=session.organization_id,
        requester_id=user_id,
        title=f"Apply seccomp profile (project soak session {str(session.id)[:8]})",
        description=f"Seccomp profile synthesized from soak session. "
                    f"Syscalls allowed: {len(profile['syscalls'][0]['names'])}. "
                    f"Partial observation: {session.partial}.",
        change_type=ChangeType.configure_seccomp,
        target_asset_ids=[str(a) for a in session.asset_ids],
        desired_outcome=params,
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
    )
    db.add(cr)
    await db.flush()
    return cr


async def _upsert_baseline(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    cr_id: uuid.UUID,
) -> None:
    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.organization_id == session.organization_id,
            SecurityPolicyBaseline.project_id == session.project_id,
            SecurityPolicyBaseline.policy_type == session.policy_type,
        )
    )
    baseline = result.scalar_one_or_none()
    if baseline:
        baseline.profile = profile
        baseline.cr_id = cr_id
        baseline.updated_at = datetime.now(timezone.utc)
    else:
        baseline = SecurityPolicyBaseline(
            organization_id=session.organization_id,
            project_id=session.project_id,
            policy_type=session.policy_type,
            profile=profile,
            cr_id=cr_id,
        )
        db.add(baseline)
