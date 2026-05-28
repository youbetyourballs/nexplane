"""
ExecuteChangeWorkflow

Deterministic orchestration — all external I/O is delegated to Activities.
The workflow function itself must remain side-effect-free (no direct DB calls,
no time.sleep, no random) so it can be replayed safely if backed by Temporal.
"""
import logging

from app.database import AsyncSessionLocal
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
    activity_post_completion_discovery,
    activity_write_appdiscovery_metadata,
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

    # Pre-change snapshot if requested
    if data.get("snapshot_before") and data.get("change_type") not in ("create_ebs_snapshot", "agent_appdiscovery"):
        try:
            from app.connectors.executors.aws.create_ebs_snapshot import execute as _snap_execute
            for _asset_id in (data.get("target_asset_ids") or []):
                _snap_result = await _snap_execute(
                    {"description": f"pre-change-{cr_id[:8]}", "wait_for_completion": False},
                    [_asset_id], None,
                )
                logger.info(f"Pre-change snapshot: {_snap_result.get('snapshot_id')} for asset {_asset_id}")
        except Exception as _snap_err:
            logger.warning(f"Pre-change snapshot failed (non-blocking): {_snap_err}")

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

    # Soft-failure: executor returned {"failed": True, ...} with partial step_results preserved.
    # activity_execute_change wraps the executor result as {"steps": [{"result": executor_dict}]},
    # so we check both the top-level result and the first step's result.
    # NOTE: only boolean True signals soft-failure; integer counts (e.g. checkov {"failed": 5})
    # are NOT soft-failures and must not be treated as such.
    _executor_result = execution_result
    if isinstance(execution_result, dict) and execution_result.get("failed") is not True:
        _steps = execution_result.get("steps") or []
        if _steps and isinstance(_steps[0].get("result"), dict):
            _executor_result = _steps[0]["result"]
    if isinstance(_executor_result, dict) and _executor_result.get("failed") is True:
        await write_audit_event(
            organization_id=org_id,
            event_type="execution.failed",
            event_payload={"error": execution_result.get("error", "executor soft-failure")},
            actor_id=actor_id,
            change_request_id=cr_id,
        )
        await update_change_request_status(cr_id, "failed")
        if execution_run_id:
            # Store in the same nested shape as a normal completion so consumers can read step_results.
            await update_execution_run_status(
                execution_run_id, "failed",
                {"execution": execution_result, "soft_failure": True}
            )
        return

    # Post-execution: persist discovered applications to asset_metadata
    if data.get("change_type") == "agent_appdiscovery":
        await activity_write_appdiscovery_metadata(
            cr_id, data["target_asset_ids"], execution_result
        )

    if data.get("change_type") == "agent_containerize_build":
        from app.services.build_result_service import write_build_result_to_metadata
        async with AsyncSessionLocal() as post_db:
            await write_build_result_to_metadata(
                post_db, data["target_asset_ids"], execution_result
            )

    if data.get("change_type") == "agent_containerize_retire":
        from app.services.retire_service import mark_asset_retired
        async with AsyncSessionLocal() as post_db:
            await mark_asset_retired(post_db, data["target_asset_ids"], execution_result)

    if data.get("change_type") in ("change_ip", "migrate_ip"):
        from app.services.ip_change_service import update_asset_ip_metadata
        async with AsyncSessionLocal() as post_db:
            await update_asset_ip_metadata(
                post_db, data["target_asset_ids"], execution_result
            )

    if data.get("change_type") == "k8s_workload_deploy":
        from app.services.workload_deploy_service import register_workload_asset
        async with AsyncSessionLocal() as post_db:
            await register_workload_asset(post_db, data["target_asset_ids"], execution_result)

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
        await activity_post_completion_discovery(cr_id)
        # Run post-change verification checks (advisory — failures are logged, not blocking)
        _vc_list = data.get("verification_checks") or []
        if _vc_list:
            try:
                from app.services.verification_check_service import run_verification_checks as _run_vc
                _vc_results = await _run_vc(_vc_list, data.get("target_asset_ids") or [])
                _all_passed = all(r.get("passed") for r in _vc_results)
                if not _all_passed:
                    _failed = [r for r in _vc_results if not r.get("passed")]
                    logger.warning(f"Verification checks failed for CR {cr_id}: {_failed}")
                    # Advisory only — CR stays completed
            except Exception as _ve:
                logger.warning(f"Verification checks error: {_ve}")
        # Auto-close linked findings
        try:
            from app.services.finding_service import close_linked_findings as _clf
            async with AsyncSessionLocal() as _find_db:
                _closed = await _clf(_find_db, cr_id)
                if _closed > 0:
                    logger.info(f"Auto-closed {_closed} findings for CR {cr_id}")
        except Exception as _fe:
            logger.warning(f"Finding auto-closure failed: {_fe}")
        # Backup target update — fires for any CR created by a recurring backup job
        if data.get("source") == "recurring_job":
            try:
                from app.services.backup_target_service import on_backup_cr_completed
                async with AsyncSessionLocal() as _backup_db:
                    await on_backup_cr_completed(_backup_db, cr_id, execution_result)
                    await _backup_db.commit()
            except Exception as _be:
                logger.warning(f"Backup target update failed for CR {cr_id}: {_be}")
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
