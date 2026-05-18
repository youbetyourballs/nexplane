"""
Bridge between the runbook executor and the ChangeRequest execution pipeline.

create_and_execute_runbook_cr() is the main entry point:
  1. Creates a draft CR on behalf of the runbook step
  2. Plans it inline (no HTTP round-trip)
  3. Auto-approves it (the runbook trigger is the approval)
  4. Fires the execution workflow as a background asyncio task

The caller (runbook_executor.py) polls cr.status on subsequent ticks.
"""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.runbook import RunbookExecution
from app.services.change_plan_service import plan_cr, PlanBlockedError
from app.workflows.runner import WorkflowInput, start_workflow
from app.workflows.execute_change_workflow import execute_change_workflow

log = logging.getLogger(__name__)


async def create_and_execute_runbook_cr(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    """
    Create, plan, approve, and fire a ChangeRequest for one runbook step.
    Returns the CR object (status='approved', workflow task already launched).
    Raises ValueError if the change_type is unknown or PlanBlockedError if planning is blocked.
    """
    cr = await _create_draft_cr(db, execution, step_def)

    # Plan inline — raises PlanBlockedError on safety block
    try:
        await plan_cr(db, cr)
    except PlanBlockedError as exc:
        log.error("Runbook step plan blocked for execution %s step %s: %s",
                  execution.id, step_def.get("step_number"), exc.blocking_issues)
        raise ValueError(f"Plan blocked: {exc.blocking_issues}")

    # Auto-approve: the runbook trigger is itself the approval
    cr.status = ChangeRequestStatus.approved
    cr.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # Create execution run record
    workflow_id = f"wf-rb-{cr.id}-1"
    run = ExecutionRun(
        change_request_id=cr.id,
        workflow_id=workflow_id,
        status=ExecutionStatus.pending,
    )
    db.add(run)
    await db.flush()

    # Fire the workflow in the background — returns immediately
    wf_input = WorkflowInput(
        change_request_id=str(cr.id),
        organization_id=str(cr.organization_id),
        initiator_id=str(execution.triggered_by),
    )
    await start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)

    return cr


async def _create_draft_cr(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    change_type_str = step_def.get("change_type", "")
    try:
        change_type = ChangeType(change_type_str)
    except ValueError:
        raise ValueError(
            f"Unknown change_type '{change_type_str}' in runbook step '{step_def.get('name')}'. "
            f"Valid types: {[e.value for e in ChangeType]}"
        )

    parameters = dict(step_def.get("parameters") or {})
    parameters.update(execution.context)

    cr = ChangeRequest(
        organization_id=uuid.UUID(execution.runbook_snapshot["organization_id"]),
        requester_id=execution.triggered_by,
        title=f"[Runbook] {step_def['name']}",
        description=(
            f"Auto-created by runbook execution {execution.id}, "
            f"step {step_def['step_number']}: {step_def['name']}"
        ),
        change_type=change_type,
        target_asset_ids=_resolve_asset_ids(step_def.get("asset_selector")),
        desired_outcome=parameters,
        risk_level=RiskLevel.medium,
        status=ChangeRequestStatus.draft,
        source="runbook",
    )
    db.add(cr)
    await db.flush()
    return cr


def _resolve_asset_ids(asset_selector: dict | None) -> list:
    if not asset_selector:
        return []
    return [str(aid) for aid in asset_selector.get("asset_ids", [])]
