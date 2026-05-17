from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import boto3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.smoke_test_run import SmokeTestRun
from app.routers import current_user

router = APIRouter(prefix="/smoke-tests/runs", tags=["smoke-tests"])


class StartRunRequest(BaseModel):
    phases: list[str]


class SmokeRunResponse(BaseModel):
    id: str
    status: str
    phases: list[str]
    started_at: datetime
    completed_at: datetime | None
    runner_instance_id: str | None
    result_summary: dict | None
    error: str | None


@router.post("", response_model=SmokeRunResponse)
async def start_run(
    req: StartRunRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    run = SmokeTestRun(
        organization_id=user.organization_id,
        created_by=user.id,
        phases=req.phases,
        status="running",
        progress_ssm_key=f"/nexplane/smoke-runs/{uuid.uuid4()}/progress",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return _to_response(run)


@router.get("", response_model=list[SmokeRunResponse])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    result = await db.execute(
        select(SmokeTestRun)
        .where(SmokeTestRun.organization_id == user.organization_id)
        .order_by(desc(SmokeTestRun.started_at))
        .limit(20)
    )
    return [_to_response(r) for r in result.scalars().all()]


@router.get("/{run_id}", response_model=SmokeRunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    run = await _get_run_or_404(db, run_id, user.organization_id)
    return _to_response(run)


@router.delete("/{run_id}", status_code=204)
async def cancel_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    run = await _get_run_or_404(db, run_id, user.organization_id)
    if run.runner_instance_id:
        try:
            ec2 = boto3.client("ec2", region_name="us-east-1")
            ec2.terminate_instances(InstanceIds=[run.runner_instance_id])
        except Exception:
            pass  # best-effort termination
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    run = await _get_run_or_404(db, run_id, user.organization_id)
    if not run.progress_ssm_key:
        raise HTTPException(400, "Run has no progress stream")

    return StreamingResponse(
        _sse_generator(run.progress_ssm_key, run_id, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_generator(
    ssm_key: str, run_id: uuid.UUID, db: AsyncSession
) -> AsyncGenerator[str, None]:
    ssm = boto3.client("ssm", region_name="us-east-1")
    seen = 0
    while True:
        # Check if run is still active
        result = await db.execute(select(SmokeTestRun).where(SmokeTestRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run or run.status not in ("running",):
            yield "event: done\ndata: {}\n\n"
            return

        # Poll SSM for new events
        try:
            param = ssm.get_parameter(Name=ssm_key)
            events = json.loads(param["Parameter"]["Value"])
            for event in events[seen:]:
                yield f"data: {json.dumps(event)}\n\n"
                seen = len(events)
        except ssm.exceptions.ParameterNotFound:
            pass
        except Exception:
            pass

        await asyncio.sleep(2)


async def _get_run_or_404(db, run_id, org_id):
    result = await db.execute(
        select(SmokeTestRun).where(
            SmokeTestRun.id == run_id,
            SmokeTestRun.organization_id == org_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Smoke test run not found")
    return run


def _to_response(run: SmokeTestRun) -> SmokeRunResponse:
    return SmokeRunResponse(
        id=str(run.id),
        status=run.status,
        phases=run.phases,
        started_at=run.started_at,
        completed_at=run.completed_at,
        runner_instance_id=run.runner_instance_id,
        result_summary=run.result_summary,
        error=run.error,
    )
