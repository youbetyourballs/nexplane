"""
ExecuteChangeWorkflow

Deterministic orchestration — all external I/O is delegated to Activities.
The workflow function itself must remain side-effect-free (no direct DB calls,
no time.sleep, no random) so it can be replayed safely if backed by Temporal.
"""
import logging

from app.workflows.runner import WorkflowInput
from app.workflows.activities import (
    load_change_request_and_plan,
    update_change_request_status,
    update_execution_run_status,
    write_audit_event,
    activity_run_preflight_checks,
    activity_execute_change,
    activity_run_verification,
    activity_execute_rollback,
)

logger = logging.getLogger(__name__)


async def execute_change_workflow(input: WorkflowInput) -> None:
    cr_id = input.change_request_id
    org_id = input.organization_id
    actor_id = input.initiator_id

    # Step 1: Load change request and plan
    data = await load_change_request_and_plan(cr_id)
    execution_run_id = data.get("execution_run_id")

    await write_audit_event(
        organization_id=org_id,
        event_type="workflow.started",
        event_payload={"change_request_id": cr_id, "workflow": "ExecuteChangeWorkflow"},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    # Step 2: Run preflight checks
    await update_change_request_status(cr_id, "executing")
    await write_audit_event(
        organization_id=org_id,
        event_type="preflight.started",
        event_payload={"checks": [c["name"] for c in data["preflight_checks"]]},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    preflight_result = await activity_run_preflight_checks(cr_id, data["preflight_checks"])

    if not preflight_result["all_passed"]:
        failed_checks = [r for r in preflight_result["results"] if not r["passed"]]
        await write_audit_event(
            organization_id=org_id,
            event_type="preflight.failed",
            event_payload={"failed_checks": failed_checks},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        await update_change_request_status(cr_id, "failed")
        if execution_run_id:
            await update_execution_run_status(execution_run_id, "failed", {"reason": "preflight_failed", "details": failed_checks})
        return

    await write_audit_event(
        organization_id=org_id,
        event_type="preflight.passed",
        event_payload={"results": preflight_result["results"]},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    # Step 3: Execute change via mock connector
    await write_audit_event(
        organization_id=org_id,
        event_type="execution.started",
        event_payload={"change_type": data["change_type"]},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    try:
        execution_result = await activity_execute_change(
            cr_id,
            data["generated_steps"],
            data["target_asset_ids"],
        )
    except Exception as exc:
        await write_audit_event(
            organization_id=org_id,
            event_type="execution.failed",
            event_payload={"error": str(exc)},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        await update_change_request_status(cr_id, "failed")
        if execution_run_id:
            await update_execution_run_status(execution_run_id, "failed", {"error": str(exc)})
        return

    await write_audit_event(
        organization_id=org_id,
        event_type="execution.completed",
        event_payload={"result_summary": {k: v for k, v in execution_result.items() if k != "host_results"}},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    # Step 4: Run verification checks
    await update_change_request_status(cr_id, "verifying")
    await write_audit_event(
        organization_id=org_id,
        event_type="verification.started",
        event_payload={"checks": [c["name"] for c in data["verification_plan"].get("checks", [])]},
        actor_id=actor_id,
        change_request_id=cr_id,
    )

    verification_result = await activity_run_verification(cr_id, data["verification_plan"], execution_result)

    # Step 5 & 6: Complete or rollback based on verification
    if verification_result["all_passed"]:
        await write_audit_event(
            organization_id=org_id,
            event_type="verification.passed",
            event_payload={"results": verification_result["results"]},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        await update_change_request_status(cr_id, "completed")
        if execution_run_id:
            await update_execution_run_status(
                execution_run_id, "completed",
                {"execution": execution_result, "verification": verification_result},
            )
        await write_audit_event(
            organization_id=org_id,
            event_type="workflow.completed",
            event_payload={"outcome": "success"},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
    else:
        failed_verifications = [r for r in verification_result["results"] if not r["passed"]]
        await write_audit_event(
            organization_id=org_id,
            event_type="verification.failed",
            event_payload={"failed_checks": failed_verifications},
            actor_id=actor_id,
            change_request_id=cr_id,
        )

        # Trigger automatic rollback
        await write_audit_event(
            organization_id=org_id,
            event_type="rollback.started",
            event_payload={"reason": "verification_failed", "strategy": data["rollback_plan"].get("strategy")},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        if execution_run_id:
            await update_execution_run_status(execution_run_id, "rolling_back", {})

        rollback_result = await activity_execute_rollback(cr_id, data["generated_steps"], execution_result)

        await update_change_request_status(cr_id, "rolled_back")
        if execution_run_id:
            await update_execution_run_status(
                execution_run_id, "rolled_back",
                {"rollback": rollback_result, "reason": "verification_failed"},
            )
        await write_audit_event(
            organization_id=org_id,
            event_type="rollback.completed",
            event_payload={"result": rollback_result},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        await write_audit_event(
            organization_id=org_id,
            event_type="workflow.completed",
            event_payload={"outcome": "rolled_back"},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
