"""
APScheduler tick function for advancing RunbookExecution state.

Called every 30 seconds by the scheduler. Each tick inspects the current step
of every active execution and performs the appropriate action.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.runbook import RunbookExecution, RunbookStepResult

log = logging.getLogger(__name__)


async def tick_all_executions(db_factory) -> None:
    """Entry point called by APScheduler. db_factory is the AsyncSessionLocal callable."""
    async with db_factory() as db:
        result = await db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.status.in_(["running", "waiting_human"]))
            .options(selectinload(RunbookExecution.step_results))
        )
        executions = result.scalars().all()
        for execution in executions:
            try:
                await tick_execution(db, execution)
            except Exception as e:
                log.error("Executor error for execution %s: %s", execution.id, e, exc_info=True)
        await db.commit()


async def tick_execution(db: AsyncSession, execution: RunbookExecution) -> None:
    steps = execution.runbook_snapshot.get("steps", [])
    step_map = {s["step_number"]: s for s in steps}

    current_step_def = step_map.get(execution.current_step)
    if current_step_def is None:
        # No more steps — execution complete
        execution.status = "completed"
        execution.completed_at = datetime.now(timezone.utc)
        return

    step_result = await _get_or_create_step_result(db, execution, current_step_def)

    match current_step_def["type"]:
        case "change":
            await _execute_change_step(db, execution, current_step_def, step_result)
        case "condition":
            await _execute_condition_step(db, execution, current_step_def, step_result)
        case "human_checkpoint":
            await _execute_checkpoint_step(db, execution, current_step_def, step_result)
        case "parallel_group":
            await _execute_parallel_step(db, execution, current_step_def, step_result)
        case _:
            log.error("Unknown step type %s in execution %s", current_step_def["type"], execution.id)


async def _get_or_create_step_result(
    db: AsyncSession, execution: RunbookExecution, step_def: dict
) -> RunbookStepResult:
    for sr in execution.step_results:
        if sr.step_number == step_def["step_number"]:
            return sr
    sr = RunbookStepResult(
        execution_id=execution.id,
        step_number=step_def["step_number"],
        step_name=step_def["name"],
        step_type=step_def["type"],
        status="pending",
    )
    db.add(sr)
    await db.flush()
    execution.step_results.append(sr)
    return sr


async def _execute_change_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status == "pending":
        # Import here to avoid circular imports
        from app.services.runbook_cr_bridge import create_runbook_change_request
        cr = await create_runbook_change_request(db, execution, step_def)
        step_result.change_request_ids = [str(cr.id)]
        step_result.status = "running"
        step_result.started_at = datetime.now(timezone.utc)

    elif step_result.status == "running":
        import uuid as _uuid
        cr_id = step_result.change_request_ids[0]
        cr = await db.get(ChangeRequest, _uuid.UUID(cr_id))
        if cr is None:
            step_result.status = "failed"
            step_result.error_message = f"Change request {cr_id} not found"
            await _handle_step_failure(db, execution, step_def)
            return

        if cr.status == ChangeRequestStatus.completed:
            step_result.status = "completed"
            step_result.completed_at = datetime.now(timezone.utc)
            step_result.result = {
                "exit_code": 0,
                "change_request_id": cr_id,
            }
            execution.current_step += 1

        elif cr.status in (ChangeRequestStatus.failed, ChangeRequestStatus.rejected,
                           ChangeRequestStatus.rolled_back):
            step_result.status = "failed"
            step_result.error_message = f"Change request ended with status: {cr.status}"
            step_result.result = {"exit_code": 1, "change_request_id": cr_id}
            await _handle_step_failure(db, execution, step_def)


async def _execute_condition_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status != "pending":
        return

    # Build restricted namespace from completed step results
    steps_ns = {sr.step_number: sr.result for sr in execution.step_results}
    namespace = {"steps": steps_ns, "ctx": execution.context, "__builtins__": {}}

    try:
        result = bool(eval(step_def["condition_expr"], namespace))  # noqa: S307
    except Exception as e:
        step_result.status = "failed"
        step_result.error_message = f"Condition eval error: {e}"
        await _handle_step_failure(db, execution, step_def)
        return

    next_step = step_def["on_true_step"] if result else step_def["on_false_step"]
    step_result.status = "completed"
    step_result.completed_at = datetime.now(timezone.utc)
    step_result.result = {"evaluated_to": result, "jumped_to_step": next_step}
    # step_number 99 is convention for "end of runbook" in templates
    execution.current_step = next_step if next_step is not None else 9999


async def _execute_checkpoint_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status == "pending":
        step_result.status = "waiting_human"
        step_result.started_at = datetime.now(timezone.utc)
        execution.status = "waiting_human"
        # Notification is best-effort; failures are logged, not fatal
        try:
            from app.services.runbook_notifications import send_checkpoint_notification
            await send_checkpoint_notification(execution, step_def)
        except Exception as e:
            log.warning("Failed to send checkpoint notification: %s", e)
        return

    if step_result.status == "waiting_human":
        timeout_hours = step_def.get("timeout_hours")
        if timeout_hours and step_result.started_at:
            elapsed_h = (
                datetime.now(timezone.utc) - step_result.started_at
            ).total_seconds() / 3600
            if elapsed_h >= timeout_hours:
                on_timeout = step_def.get("on_timeout", "abort")
                now = datetime.now(timezone.utc)
                if on_timeout == "abort":
                    step_result.status = "failed"
                    step_result.error_message = "Human checkpoint timed out"
                    step_result.completed_at = now
                    execution.status = "failed"
                    execution.completed_at = now
                else:  # "continue"
                    step_result.status = "completed"
                    step_result.completed_at = now
                    step_result.result = {"action": "timeout_continue"}
                    execution.status = "running"
                    execution.current_step += 1
    # If status is "completed", the resume endpoint already advanced current_step — nothing to do.


async def _execute_parallel_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    child_steps = step_def.get("parallel_steps", [])

    if step_result.status == "pending":
        from app.services.runbook_cr_bridge import create_runbook_change_request
        cr_ids = []
        for child in child_steps:
            cr = await create_runbook_change_request(db, execution, child)
            cr_ids.append(str(cr.id))
        step_result.change_request_ids = cr_ids
        step_result.status = "running"
        step_result.started_at = datetime.now(timezone.utc)
        return

    if step_result.status == "running":
        import uuid as _uuid
        all_done = True
        any_failed = False
        child_results = []
        for cr_id in step_result.change_request_ids:
            cr = await db.get(ChangeRequest, _uuid.UUID(cr_id))
            terminal = cr and cr.status in (
                ChangeRequestStatus.completed, ChangeRequestStatus.failed,
                ChangeRequestStatus.rejected, ChangeRequestStatus.rolled_back,
            )
            if not terminal:
                all_done = False
            if cr and cr.status != ChangeRequestStatus.completed:
                any_failed = True
            child_results.append({"cr_id": cr_id, "status": cr.status if cr else "unknown"})

        if not all_done:
            return

        step_result.result = {"child_results": child_results}
        step_result.completed_at = datetime.now(timezone.utc)

        if any_failed:
            step_result.status = "failed"
            await _handle_step_failure(db, execution, step_def)
        else:
            step_result.status = "completed"
            execution.current_step += 1


async def _handle_step_failure(
    db: AsyncSession, execution: RunbookExecution, step_def: dict
) -> None:
    on_failure = step_def.get("on_failure", "abort")
    now = datetime.now(timezone.utc)
    if on_failure == "abort":
        execution.status = "failed"
        execution.completed_at = now
    elif on_failure == "continue":
        execution.current_step += 1
    elif on_failure == "rollback_all":
        # Rollback hook: create rollback change requests in reverse order.
        # Full rollback CR support is deferred — log and fail for now.
        log.warning(
            "rollback_all requested for execution %s but rollback CRs are not yet implemented; marking failed.",
            execution.id,
        )
        execution.status = "rolled_back"
        execution.completed_at = now
