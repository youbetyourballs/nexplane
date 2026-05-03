import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.runbook import Runbook, RunbookStep, RunbookExecution, RunbookStepResult
from app.schemas.runbook import RunbookCreate, RunbookUpdate, RunbookOut

log = logging.getLogger(__name__)

_RB_OPTIONS = [selectinload(Runbook.steps).selectinload(RunbookStep.children)]
_EXEC_OPTIONS = [selectinload(RunbookExecution.step_results)]


class RunbookService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Runbook CRUD ---

    async def list_runbooks(self, org_id: uuid.UUID, *, tag: str | None = None,
                            search: str | None = None) -> list[Runbook]:
        q = select(Runbook).where(Runbook.organization_id == org_id).options(*_RB_OPTIONS)
        if tag:
            q = q.where(Runbook.tags.any(tag))
        if search:
            q = q.where(Runbook.name.ilike(f"%{search}%"))
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def get_runbook(self, runbook_id: str, org_id: uuid.UUID) -> Runbook:
        result = await self.db.execute(
            select(Runbook)
            .where(Runbook.id == uuid.UUID(runbook_id), Runbook.organization_id == org_id)
            .options(*_RB_OPTIONS)
        )
        rb = result.scalar_one_or_none()
        if not rb:
            raise HTTPException(status_code=404, detail="Runbook not found")
        return rb

    async def create_runbook(self, org_id: uuid.UUID, user_id: uuid.UUID,
                             payload: RunbookCreate) -> Runbook:
        rb = Runbook(
            organization_id=org_id,
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            version=1,
            is_seed=False,
            created_by=user_id,
        )
        self.db.add(rb)
        await self.db.flush()
        await self._insert_steps(payload.steps, rb.id, parent_id=None)
        await self.db.commit()
        await self.db.refresh(rb)
        return await self.get_runbook(str(rb.id), org_id)

    async def update_runbook(self, runbook_id: str, org_id: uuid.UUID,
                             payload: RunbookUpdate) -> Runbook:
        rb = await self.get_runbook(runbook_id, org_id)
        if payload.name is not None:
            rb.name = payload.name
        if payload.description is not None:
            rb.description = payload.description
        if payload.tags is not None:
            rb.tags = payload.tags
        if payload.steps is not None:
            # Replace all steps atomically
            await self.db.execute(
                RunbookStep.__table__.delete().where(RunbookStep.runbook_id == rb.id)
            )
            await self._insert_steps(payload.steps, rb.id, parent_id=None)
        rb.version += 1
        await self.db.commit()
        return await self.get_runbook(runbook_id, org_id)

    async def delete_runbook(self, runbook_id: str, org_id: uuid.UUID) -> None:
        rb = await self.get_runbook(runbook_id, org_id)
        # Reject if active executions exist
        active = await self.db.execute(
            select(RunbookExecution).where(
                RunbookExecution.runbook_id == rb.id,
                RunbookExecution.status.in_(["running", "waiting_human"]),
            )
        )
        if active.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Cannot delete runbook with active executions")
        await self.db.delete(rb)
        await self.db.commit()

    async def fork_runbook(self, runbook_id: str, org_id: uuid.UUID,
                           user_id: uuid.UUID) -> Runbook:
        # Seed templates can be forked even from a different org (they are global)
        result = await self.db.execute(
            select(Runbook).where(Runbook.id == uuid.UUID(runbook_id)).options(*_RB_OPTIONS)
        )
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Runbook not found")

        new_rb = Runbook(
            organization_id=org_id,
            name=f"{source.name} (copy)",
            description=source.description,
            tags=list(source.tags),
            version=1,
            is_seed=False,
            created_by=user_id,
        )
        self.db.add(new_rb)
        await self.db.flush()
        await self._copy_steps(source.steps, new_rb.id, parent_id=None)
        await self.db.commit()
        return await self.get_runbook(str(new_rb.id), org_id)

    # --- Trigger ---

    async def trigger_runbook(self, runbook_id: str, org_id: uuid.UUID,
                              user_id: uuid.UUID, context: dict) -> RunbookExecution:
        rb = await self.get_runbook(runbook_id, org_id)
        snapshot = RunbookOut.model_validate(rb).model_dump(mode="json")
        # Embed org_id in snapshot for executor
        snapshot["organization_id"] = str(org_id)
        execution = RunbookExecution(
            runbook_id=rb.id,
            runbook_version=rb.version,
            runbook_snapshot=snapshot,
            triggered_by=user_id,
            context=context,
            status="running",
            current_step=1,
        )
        self.db.add(execution)
        await self.db.commit()
        await self.db.refresh(execution)
        return await self.get_execution(str(execution.id), org_id)

    # --- Execution queries ---

    async def list_executions(self, runbook_id: str, org_id: uuid.UUID) -> list[RunbookExecution]:
        # Verify runbook belongs to org
        await self.get_runbook(runbook_id, org_id)
        result = await self.db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.runbook_id == uuid.UUID(runbook_id))
            .options(*_EXEC_OPTIONS)
            .order_by(RunbookExecution.triggered_at.desc())
        )
        return list(result.scalars().all())

    async def get_execution(self, execution_id: str, org_id: uuid.UUID) -> RunbookExecution:
        result = await self.db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.id == uuid.UUID(execution_id))
            .options(*_EXEC_OPTIONS, selectinload(RunbookExecution.runbook))
        )
        ex = result.scalar_one_or_none()
        if not ex or ex.runbook.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        return ex

    async def resume_checkpoint(self, execution_id: str, step_number: int,
                                action: str, user) -> RunbookExecution:
        ex = await self.get_execution(execution_id, user.organization_id)
        if ex.status != "waiting_human":
            raise HTTPException(status_code=409, detail="Execution is not waiting for human input")

        # Find the waiting step result
        result = await self.db.execute(
            select(RunbookStepResult).where(
                RunbookStepResult.execution_id == ex.id,
                RunbookStepResult.step_number == step_number,
                RunbookStepResult.status == "waiting_human",
            )
        )
        step_result = result.scalar_one_or_none()
        if not step_result:
            raise HTTPException(status_code=404, detail="Waiting step not found")

        now = datetime.now(timezone.utc)
        if action == "resume":
            step_result.status = "completed"
            step_result.completed_at = now
            step_result.result = {
                "action": "resumed",
                "resumed_by": str(user.id),
                "resumed_at": now.isoformat(),
            }
            ex.status = "running"
            ex.current_step = step_number + 1
        elif action == "abort":
            step_result.status = "failed"
            step_result.error_message = f"Aborted by {user.email}"
            ex.status = "failed"
            ex.completed_at = now
        else:
            raise HTTPException(status_code=400, detail="action must be 'resume' or 'abort'")

        await self.db.commit()
        return await self.get_execution(execution_id, user.organization_id)

    async def abort_execution(self, execution_id: str, org_id: uuid.UUID) -> RunbookExecution:
        ex = await self.get_execution(execution_id, org_id)
        if ex.status not in ("running", "waiting_human"):
            raise HTTPException(status_code=409, detail="Execution is not active")
        now = datetime.now(timezone.utc)
        ex.status = "failed"
        ex.completed_at = now
        # Mark current in-progress step result as failed
        result = await self.db.execute(
            select(RunbookStepResult).where(
                RunbookStepResult.execution_id == ex.id,
                RunbookStepResult.step_number == ex.current_step,
            )
        )
        sr = result.scalar_one_or_none()
        if sr:
            sr.status = "failed"
            sr.error_message = "Aborted by operator"
        await self.db.commit()
        return await self.get_execution(execution_id, org_id)

    # --- Internal helpers ---

    async def _insert_steps(self, steps, runbook_id: uuid.UUID,
                            parent_id: uuid.UUID | None) -> None:
        for s in steps:
            step = RunbookStep(
                runbook_id=runbook_id,
                parent_step_id=parent_id,
                step_number=s.step_number,
                name=s.name,
                type=s.type,
                change_type=s.change_type,
                parameters=s.parameters,
                asset_selector=s.asset_selector.model_dump() if s.asset_selector else None,
                condition_expr=s.condition_expr,
                on_true_step=s.on_true_step,
                on_false_step=s.on_false_step,
                prompt=s.prompt,
                required_role=s.required_role,
                timeout_hours=s.timeout_hours,
                on_timeout=s.on_timeout,
                on_failure=s.on_failure,
            )
            self.db.add(step)
            await self.db.flush()
            if s.parallel_steps:
                await self._insert_steps(s.parallel_steps, runbook_id, step.id)

    async def _copy_steps(self, steps, runbook_id: uuid.UUID,
                          parent_id: uuid.UUID | None) -> None:
        for s in steps:
            new_step = RunbookStep(
                runbook_id=runbook_id,
                parent_step_id=parent_id,
                step_number=s.step_number,
                name=s.name,
                type=s.type,
                change_type=s.change_type,
                parameters=s.parameters,
                asset_selector=s.asset_selector,
                condition_expr=s.condition_expr,
                on_true_step=s.on_true_step,
                on_false_step=s.on_false_step,
                prompt=s.prompt,
                required_role=s.required_role,
                timeout_hours=s.timeout_hours,
                on_timeout=s.on_timeout,
                on_failure=s.on_failure,
            )
            self.db.add(new_step)
            await self.db.flush()
            if s.children:
                await self._copy_steps(s.children, runbook_id, new_step.id)
