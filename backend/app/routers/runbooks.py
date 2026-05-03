from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers import current_user
from app.schemas.runbook import (
    RunbookCreate, RunbookUpdate, RunbookOut, RunbookExecutionOut,
    TriggerRunbookRequest, HumanCheckpointResumeRequest,
)
from app.services.runbook_service import RunbookService

router = APIRouter(prefix="/api/runbooks", tags=["runbooks"])
execution_router = APIRouter(prefix="/api/executions", tags=["runbook-executions"])


@router.get("", response_model=list[RunbookOut])
async def list_runbooks(
    tag: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).list_runbooks(user.organization_id, tag=tag, search=search)


@router.post("", response_model=RunbookOut, status_code=201)
async def create_runbook(
    payload: RunbookCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).create_runbook(user.organization_id, user.id, payload)


@router.get("/{runbook_id}", response_model=RunbookOut)
async def get_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).get_runbook(runbook_id, user.organization_id)


@router.put("/{runbook_id}", response_model=RunbookOut)
async def update_runbook(
    runbook_id: str,
    payload: RunbookUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).update_runbook(runbook_id, user.organization_id, payload)


@router.delete("/{runbook_id}", status_code=204)
async def delete_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await RunbookService(db).delete_runbook(runbook_id, user.organization_id)


@router.post("/{runbook_id}/fork", response_model=RunbookOut, status_code=201)
async def fork_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).fork_runbook(runbook_id, user.organization_id, user.id)


@router.post("/{runbook_id}/trigger", response_model=RunbookExecutionOut, status_code=201)
async def trigger_runbook(
    runbook_id: str,
    payload: TriggerRunbookRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).trigger_runbook(
        runbook_id, user.organization_id, user.id, payload.context
    )


@router.get("/{runbook_id}/executions", response_model=list[RunbookExecutionOut])
async def list_executions(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).list_executions(runbook_id, user.organization_id)


@execution_router.get("/{execution_id}", response_model=RunbookExecutionOut)
async def get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).get_execution(execution_id, user.organization_id)


@execution_router.post("/{execution_id}/resume", response_model=RunbookExecutionOut)
async def resume_execution(
    execution_id: str,
    payload: HumanCheckpointResumeRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).resume_checkpoint(
        execution_id, payload.step_number, payload.action, user
    )


@execution_router.post("/{execution_id}/abort", response_model=RunbookExecutionOut)
async def abort_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).abort_execution(execution_id, user.organization_id)
