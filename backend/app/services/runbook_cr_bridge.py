"""
Thin bridge between the runbook executor and the existing ChangeRequest creation flow.
Isolates the executor from ChangeRequest model details.
"""
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.runbook import RunbookExecution


async def create_runbook_change_request(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    """
    Create a ChangeRequest on behalf of a runbook step.
    Maps step_def["change_type"] to the ChangeType enum; falls back to a best-effort
    match. If the change_type is not a known enum value, raises ValueError.
    """
    change_type_str = step_def.get("change_type", "")
    try:
        change_type = ChangeType(change_type_str)
    except ValueError:
        raise ValueError(
            f"Unknown change_type '{change_type_str}' in runbook step '{step_def.get('name')}'. "
            f"Valid types: {[e.value for e in ChangeType]}"
        )

    parameters = step_def.get("parameters") or {}
    parameters.update(execution.context)  # runtime context overrides step defaults

    cr = ChangeRequest(
        organization_id=uuid.UUID(execution.runbook_snapshot["organization_id"]),
        requester_id=execution.triggered_by,
        title=f"[Runbook] {step_def['name']}",
        description=(
            f"Created automatically by runbook execution {execution.id}, "
            f"step {step_def['step_number']}: {step_def['name']}"
        ),
        change_type=change_type,
        target_asset_ids=_resolve_asset_ids(step_def.get("asset_selector")),
        desired_outcome=parameters,
        risk_level=RiskLevel.medium,
        status=ChangeRequestStatus.draft,
        source="runbook",
        runbook_execution_id=execution.id,
    )
    db.add(cr)
    await db.flush()
    return cr


def _resolve_asset_ids(asset_selector: dict | None) -> list:
    """Extract explicit asset_ids from asset_selector. Tag/env resolution is deferred."""
    if not asset_selector:
        return []
    return [str(aid) for aid in asset_selector.get("asset_ids", [])]
