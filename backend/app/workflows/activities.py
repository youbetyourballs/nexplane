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
            "generated_steps": plan.generated_steps if plan else [],
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
    generated_steps: list[dict],
    asset_ids: list[str],
) -> dict:
    from app.services.connector_service import execute_action

    step_results = []
    # Carries resolved values forward from steps like resolve_launch_config
    # so subsequent steps can use them without being pre-planned with real values.
    carried_context: dict = {}

    async with AsyncSessionLocal() as db:
        for step in generated_steps:
            connector_type = step.get("connector_type", "")
            action_id = step.get("action_id", "")
            # Merge step parameters with carried context — carried context wins for keys
            # where the plan value is empty/None (handles resolve_launch_config → launch_instance)
            plan_params = step.get("parameters", {})
            parameters = {
                **plan_params,
                **{k: v for k, v in carried_context.items() if not plan_params.get(k)},
            }
            step_connector_id = step.get("connector_id")

            # Look up the specific connector instance if we have its ID
            connector = None
            if step_connector_id:
                result = await db.execute(
                    select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
                )
                connector = result.scalar_one_or_none()

            # Use the actual connector type from the DB record when the plan stored "unknown"
            if connector and (not connector_type or connector_type == "unknown"):
                connector_type = connector.connector_type.value

            try:
                result = await execute_action(
                    connector_type, action_id, parameters, asset_ids,
                    connector=connector, db=db if connector else None,
                )
            except Exception as exc:
                logger.error("Step %s failed: %s", step.get("step_number"), exc)
                raise

            step_results.append({
                "step_number": step["step_number"],
                "generic_action": step.get("generic_action"),
                "action_id": action_id,
                "connector_type": connector_type,
                "connector_id": step_connector_id,
                "result": result,
            })
            # Propagate any step's scalar outputs into subsequent steps' parameters.
            # This lets resolve_launch_config feed ami_id/subnet_id into launch_instance,
            # and launch_instance feed instance_id into wait_instance_state, etc.
            if isinstance(result, dict) and "error" not in result:
                carried_context.update({
                    k: v for k, v in result.items()
                    if k not in ("action", "_auto_asset") and isinstance(v, (str, int, float, bool, list))
                })
            logger.info("Step %s (%s) completed", step.get("step_number"), action_id)

    logger.info("All steps completed for change request %s", change_request_id)
    return {"steps": step_results}


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
    generated_steps: list[dict],
    execution_result: dict,
) -> dict:
    from app.services.connector_service import execute_action

    rollback_results = []

    # Build a lookup of step results from the execution so rollback steps can
    # extract values like instance_id that weren't known at plan time.
    # The stored result may be {"execution": {"steps": [...]}} or {"steps": [...]} directly.
    steps_list = (
        execution_result.get("execution", {}).get("steps")
        or execution_result.get("steps")
        or []
    )
    step_result_by_number: dict[int, dict] = {}
    for step_rec in steps_list:
        step_result_by_number[step_rec["step_number"]] = step_rec.get("result", {})

    async with AsyncSessionLocal() as db:
        for step in reversed(generated_steps):
            rollback_action = step.get("rollback_action")
            rollback_connector = step.get("rollback_connector_type")
            if not rollback_action or not rollback_connector:
                continue

            step_connector_id = step.get("connector_id")
            connector = None
            if step_connector_id:
                result = await db.execute(
                    select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
                )
                connector = result.scalar_one_or_none()

            if connector and (not rollback_connector or rollback_connector == "unknown"):
                rollback_connector = connector.connector_type.value

            # Merge this step's own execution result into rollback parameters so
            # values resolved at runtime (e.g. instance_id) are available.
            prior_result = step_result_by_number.get(step["step_number"], {})
            rollback_params = {
                **prior_result,
                "confirm_terminate": True,  # rollback implies confirmation
            }

            try:
                result = await execute_action(
                    rollback_connector, rollback_action, rollback_params, [],
                    connector=connector, db=db if connector else None,
                )
                # Remove the asset from inventory if the rollback terminated an instance
                instance_id = prior_result.get("instance_id") or rollback_params.get("instance_id")
                if rollback_action == "terminate_instance" and instance_id and connector:
                    from sqlalchemy import delete as sa_delete
                    from app.models.asset import Asset
                    await db.execute(
                        sa_delete(Asset).where(
                            Asset.organization_id == connector.organization_id,
                            Asset.asset_metadata["instance_id"].as_string() == instance_id,
                        )
                    )
                    await db.commit()
                    logger.info("Removed asset for terminated instance %s", instance_id)
            except Exception as exc:
                logger.error("Rollback step %s failed: %s", step.get("step_number"), exc)
                result = {"rolled_back": False, "error": str(exc)}

            rollback_results.append({
                "step_number": step["step_number"],
                "rollback_action": rollback_action,
                "result": result,
            })

    logger.info("Rollback complete for %s", change_request_id)
    return {"rollback_steps": rollback_results}


# Change types that should trigger a discovery re-sync after completion
_DISCOVERY_CHANGE_TYPES = {
    "ec2_launch", "ec2_terminate", "ec2_stop", "ec2_start", "ec2_stop_start",
}

_CONNECTOR_DISCOVERY_ACTION = {
    "aws": "discover_ec2_instances",
}


async def activity_post_completion_discovery(change_request_id: str) -> None:
    """After certain change types complete, re-run discovery on the relevant connector
    so new or modified assets appear in inventory without manual intervention."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == uuid.UUID(change_request_id))
            .options(selectinload(ChangeRequest.change_plan))
        )
        cr = result.scalar_one_or_none()
        if not cr or cr.change_type.value not in _DISCOVERY_CHANGE_TYPES:
            return

        # Find the connector used in the plan steps
        plan = cr.change_plan
        connector_id = None
        if plan:
            for step in plan.generated_steps:
                if step.get("connector_id"):
                    connector_id = step["connector_id"]
                    break

        if not connector_id:
            # Fall back: find any AWS connector in the org
            conn_result = await db.execute(
                select(Connector).where(
                    Connector.organization_id == cr.organization_id,
                    Connector.connector_type == ConnectorType.aws,
                )
            )
            connector = conn_result.scalars().first()
        else:
            conn_result = await db.execute(
                select(Connector).where(Connector.id == uuid.UUID(connector_id))
            )
            connector = conn_result.scalar_one_or_none()

        if not connector:
            logger.info("No connector found for post-completion discovery on %s", change_request_id)
            return

        action_id = _CONNECTOR_DISCOVERY_ACTION.get(connector.connector_type.value)
        if not action_id:
            return

        try:
            from app.services.ingest_service import IngestService
            from app.connectors.catalog_service import get_catalog_service
            service = IngestService(get_catalog_service())
            await service.run(action_id, connector, cr.organization_id, db)
            await db.commit()
            logger.info("Post-completion discovery ran for %s (%s)", change_request_id, action_id)
        except Exception as exc:
            logger.warning("Post-completion discovery failed for %s: %s", change_request_id, exc)
