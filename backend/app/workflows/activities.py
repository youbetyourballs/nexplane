"""
Workflow Activities — all external I/O lives here.

Each activity is a standalone async function. In a Temporal deployment these
would be decorated with @activity.defn and run inside a Worker. For the MVP
runner they are called directly as coroutines from the workflow.
"""
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.change_plan import ChangePlan
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.audit_event import AuditEvent
from app.models.connector import Connector, ConnectorType
from app.services import connector_service

logger = logging.getLogger(__name__)


async def load_change_request_and_plan(change_request_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == uuid.UUID(change_request_id))
            .options(selectinload(ChangeRequest.change_plan), selectinload(ChangeRequest.execution_runs))
        )
        cr = result.scalar_one_or_none()
        if not cr:
            raise ValueError(f"ChangeRequest {change_request_id} not found")

        plan = cr.change_plan
        latest_run = sorted(cr.execution_runs, key=lambda r: r.started_at, reverse=True)[0] if cr.execution_runs else None

        return {
            "change_request_id": str(cr.id),
            "organization_id": str(cr.organization_id),
            "change_type": cr.change_type.value,
            "desired_outcome": cr.desired_outcome,
            "target_asset_ids": cr.target_asset_ids,
            "risk_level": cr.risk_level.value,
            "plan_id": str(plan.id) if plan else None,
            "preflight_checks": plan.preflight_checks if plan else [],
            "rollback_plan": plan.rollback_plan if plan else {},
            "verification_plan": plan.verification_plan if plan else {},
            "execution_run_id": str(latest_run.id) if latest_run else None,
        }


async def update_change_request_status(change_request_id: str, status: str) -> None:
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, uuid.UUID(change_request_id))
        if cr:
            cr.status = ChangeRequestStatus(status)
            cr.updated_at = datetime.now(timezone.utc)
            await db.commit()


async def update_execution_run_status(
    execution_run_id: str,
    status: str,
    result: dict | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        run = await db.get(ExecutionRun, uuid.UUID(execution_run_id))
        if run:
            run.status = ExecutionStatus(status)
            if result is not None:
                run.result = result
            if status in ("completed", "failed", "rolled_back"):
                run.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def write_audit_event(
    organization_id: str,
    event_type: str,
    event_payload: dict,
    actor_id: str | None = None,
    change_request_id: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        event = AuditEvent(
            organization_id=uuid.UUID(organization_id),
            actor_id=uuid.UUID(actor_id) if actor_id else None,
            change_request_id=uuid.UUID(change_request_id) if change_request_id else None,
            event_type=event_type,
            event_payload=event_payload,
        )
        db.add(event)
        await db.commit()


async def activity_run_preflight_checks(
    change_request_id: str,
    preflight_checks: list[dict],
) -> dict:
    result = await connector_service.run_preflight_checks(preflight_checks)
    logger.info("Preflight checks for %s: all_passed=%s", change_request_id, result["all_passed"])
    return result


async def activity_execute_change(
    change_request_id: str,
    change_type: str,
    desired_outcome: dict,
    asset_ids: list[str],
) -> dict:
    from app.models.change_request import ChangeType

    async with AsyncSessionLocal() as db:
        result_rows = await db.execute(
            select(Connector).where(Connector.status == "active").limit(1)
        )
        connector = result_rows.scalar_one_or_none()
        connector_type = connector.connector_type if connector else ConnectorType.aws_mock

    result = await connector_service.execute_change(
        ChangeType(change_type),
        desired_outcome,
        asset_ids,
        connector_type,
    )
    logger.info("Execution for %s completed: %s", change_request_id, result)
    return result


async def activity_run_verification(
    change_request_id: str,
    verification_plan: dict,
    execution_result: dict,
) -> dict:
    result = await connector_service.run_verification_checks(verification_plan, execution_result)
    logger.info("Verification for %s: all_passed=%s", change_request_id, result["all_passed"])
    return result


async def activity_execute_rollback(
    change_request_id: str,
    rollback_plan: dict,
    execution_result: dict,
) -> dict:
    result = await connector_service.execute_rollback(
        None,
        rollback_plan,
        execution_result,
    )
    logger.info("Rollback for %s: %s", change_request_id, result)
    return result
