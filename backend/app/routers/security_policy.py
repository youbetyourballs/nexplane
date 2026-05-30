# backend/app/routers/security_policy.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline
from app.models.user import User
from app.routers import current_user
from app.schemas.security_policy import (
    AcceptDiffBody, BaselineRead, SoakSessionCreate, SoakSessionRead, SoakSessionStopBody,
)
from app.services.security_policy import soak_service

router = APIRouter(prefix="/security-policy", tags=["Security Policy"])


@router.post("/soak-sessions", response_model=SoakSessionRead, status_code=201)
async def start_soak_session(
    body: SoakSessionCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await soak_service.start_session(
        db=db,
        organization_id=user.organization_id,
        project_id=body.project_id,
        policy_type=body.policy_type,
        asset_ids=[str(a) for a in body.asset_ids],
        window_seconds=body.window_seconds,
        user_id=user.id,
    )
    return session


@router.get("/soak-sessions/{session_id}", response_model=SoakSessionRead)
async def get_soak_session(
    session_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    return session


@router.post("/soak-sessions/{session_id}/stop", response_model=SoakSessionRead)
async def stop_soak_session(
    session_id: uuid.UUID,
    body: SoakSessionStopBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    if session.status != "running":
        raise HTTPException(status_code=409, detail=f"Session is not running (status={session.status})")
    session = await soak_service.stop_and_synthesize(
        db=db, session=session, service_name=body.service_name, user_id=user.id
    )
    return session


@router.get("/soak-sessions/{session_id}/diff")
async def get_diff(
    session_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="Session is still running")
    if session.baseline_delta is None:
        raise HTTPException(status_code=404, detail="No prior baseline — session will auto-propose CR")
    return session.baseline_delta


@router.post("/soak-sessions/{session_id}/accept", response_model=SoakSessionRead)
async def accept_diff(
    session_id: uuid.UUID,
    body: AcceptDiffBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    try:
        session = await soak_service.accept_diff(
            db=db, session=session, service_name=body.service_name, user_id=user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return session


@router.get("/baselines/{project_id}", response_model=BaselineRead)
async def get_baseline(
    project_id: uuid.UUID,
    policy_type: str = "seccomp",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.project_id == project_id,
            SecurityPolicyBaseline.organization_id == user.organization_id,
            SecurityPolicyBaseline.policy_type == policy_type,
        )
    )
    baseline = result.scalar_one_or_none()
    if not baseline:
        raise HTTPException(status_code=404, detail="No baseline for this project")
    return baseline
