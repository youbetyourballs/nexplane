# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
            # FILO rollback stack: stamp application_sequence when a CR completes
            if status == "completed" and cr.application_sequence is None:
                from sqlalchemy import func as _func
                from app.models.change_request import ChangeRequest as _CR
                # Find the max sequence across all CRs touching any of the same assets
                asset_ids = [str(a) for a in (cr.target_asset_ids or [])]
                if asset_ids:
                    from sqlalchemy import String, type_coerce
                    from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY
                    from sqlalchemy.dialects.postgresql import JSONB as _JSONB
                    # Use a subquery to find max sequence among CRs that overlap this asset set
                    # ?| requires text[] on the right — type_coerce tells SQLAlchemy the param type
                    existing = await db.execute(
                        select(_func.max(_CR.application_sequence)).where(
                            _CR.id != cr.id,
                            _CR.application_sequence.isnot(None),
                            _CR.target_asset_ids.cast(_JSONB).op("?|")(
                                type_coerce(asset_ids, _PG_ARRAY(String))
                            ),
                        )
                    )
                    max_seq = existing.scalar_one_or_none()
                    cr.application_sequence = (max_seq or 0) + 1
                    cr.applied_at = datetime.now(timezone.utc)
            await db.commit()
    # Notify project rollback service if this CR belongs to a project and has failed
    if status == "failed":
        try:
            import asyncio as _asyncio
            from sqlalchemy import select as _sa_select
            from app.models.project import ProjectChangeRequest as _PCR
            from app.services.project_rollback_service import on_cr_failed as _on_cr_failed
            _cr_uuid = uuid.UUID(change_request_id)
            async with AsyncSessionLocal() as _db:
                _pcr_res = await _db.execute(
                    _sa_select(_PCR).where(_PCR.change_request_id == _cr_uuid)
                )
                _pcr = _pcr_res.scalar_one_or_none()
                if _pcr:
                    _asyncio.ensure_future(_on_cr_failed(_pcr.project_id, _cr_uuid))
        except Exception:
            pass  # never block CR status update on rollback notification failure


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
    from app.connectors.executors.identity.fan_out_registry import is_fan_out_change_type

    # Fan-out branch: bypass step-loop for identity fan-out change types
    async with AsyncSessionLocal() as _fan_db:
        _cr_r = await _fan_db.execute(
            select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
        )
        _cr = _cr_r.scalar_one_or_none()
        if _cr and is_fan_out_change_type(_cr.change_type.value):
            from app.connectors.executors.identity import fan_out_executor
            _result = await fan_out_executor.execute(
                parameters=_cr.desired_outcome or {},
                asset_ids=[str(a) for a in (_cr.target_asset_ids or [])],
                connector=None,
                change_request_id=_cr.id,
                change_type=_cr.change_type.value,
                db=_fan_db,
            )
            return _result

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
                if connector:
                    await connector_service._attach_credentials(connector, db)

            # Fallback: if no locked connector but connector_type is known, look up any
            # active connector of that type in the org (needed for wazuh/falco/infisical phases
            # where the plan is generated without a locked connector_id).
            if connector is None and connector_type and connector_type not in ("", "unknown"):
                try:
                    _ct_enum = ConnectorType(connector_type)
                    _cr_result = await db.execute(
                        select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
                    )
                    _cr_obj = _cr_result.scalar_one_or_none()
                    if _cr_obj:
                        _conn_result = await db.execute(
                            select(Connector).where(
                                Connector.organization_id == _cr_obj.organization_id,
                                Connector.connector_type == _ct_enum,
                            ).order_by(Connector.created_at.desc()).limit(1)
                        )
                        _fallback = _conn_result.scalar_one_or_none()
                        if _fallback:
                            connector = _fallback
                            await connector_service._attach_credentials(connector, db)
                            logger.info("Step %s: using fallback connector %s (type=%s)",
                                        step.get("step_number"), connector.id, connector_type)
                except Exception as _lookup_exc:
                    logger.debug("Fallback connector lookup failed: %s", _lookup_exc)

            # Use the actual connector type from the DB record when the plan stored "unknown"
            if connector and (not connector_type or connector_type == "unknown"):
                connector_type = connector.connector_type.value

            try:
                result = await execute_action(
                    connector_type, action_id, parameters, asset_ids,
                    connector=connector, db=db if connector is not None else None,
                )
            except Exception as exc:
                import traceback
                logger.error("Step %s failed: %s\n%s", step.get("step_number"), exc, traceback.format_exc())
                raise

            # Commit any flushed _auto_asset upserts from execute_action
            await db.commit()

            step_results.append({
                "step_number": step["step_number"],
                "generic_action": step.get("generic_action"),
                "action_id": action_id,
                "connector_type": connector_type,
                "connector_id": str(connector.id) if connector else step_connector_id,
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
    verification_result = await connector_service.run_verification_checks(verification_plan, execution_result)
    logger.info("Verification for %s: all_passed=%s", change_request_id, verification_result["all_passed"])

    # Persist explicit verification_status to the CR
    from app.models.change_request import VerificationStatus as _VS
    _vs_map = {
        "passed": _VS.passed,
        "failed": _VS.failed,
        "unsupported": _VS.unsupported,
        "manual_required": _VS.manual_required,
        "skipped": _VS.skipped_development_only,
        "skipped_development_only": _VS.skipped_development_only,
    }
    _vs_raw = verification_result.get("status", "unsupported") if isinstance(verification_result, dict) else "unsupported"
    async with AsyncSessionLocal() as _vdb:
        _cr_obj = await _vdb.get(ChangeRequest, uuid.UUID(change_request_id))
        if _cr_obj:
            _cr_obj.verification_status = _vs_map.get(_vs_raw, _VS.unsupported)
            await _vdb.commit()

    return verification_result


async def activity_execute_rollback(
    change_request_id: str,
    generated_steps: list[dict],
    execution_result: dict,
) -> dict:
    from app.services.connector_service import execute_action
    from app.connectors.executors.identity.fan_out_registry import is_fan_out_change_type

    # Fan-out rollback branch
    async with AsyncSessionLocal() as _fan_db:
        _cr_r = await _fan_db.execute(
            select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
        )
        _cr = _cr_r.scalar_one_or_none()
        if _cr and is_fan_out_change_type(_cr.change_type.value):
            from app.connectors.executors.identity import fan_out_executor
            return await fan_out_executor.rollback(
                parameters=_cr.desired_outcome or {},
                execution_result=execution_result,
                connector=None,
            )

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

            # Fallback: look up any active connector of the rollback type in the org
            # when the plan step has no locked connector_id (same pattern as execute path).
            if connector is None and rollback_connector and rollback_connector not in ("", "unknown"):
                try:
                    _ct_enum = ConnectorType(rollback_connector)
                    _cr_result = await db.execute(
                        select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
                    )
                    _cr_obj = _cr_result.scalar_one_or_none()
                    if _cr_obj:
                        _conn_result = await db.execute(
                            select(Connector).where(
                                Connector.organization_id == _cr_obj.organization_id,
                                Connector.connector_type == _ct_enum,
                            ).order_by(Connector.created_at.desc()).limit(1)
                        )
                        _fallback = _conn_result.scalar_one_or_none()
                        if _fallback:
                            connector = _fallback
                            await connector_service._attach_credentials(connector, db)
                            logger.info("Rollback step %s: using fallback connector %s (type=%s)",
                                        step.get("step_number"), connector.id, rollback_connector)
                except Exception as _lookup_exc:
                    logger.debug("Rollback fallback connector lookup failed: %s", _lookup_exc)

            if connector and (not rollback_connector or rollback_connector == "unknown"):
                rollback_connector = connector.connector_type.value

            # Merge this step's own execution result into rollback parameters so
            # values resolved at runtime (e.g. instance_id) are available.
            prior_result = step_result_by_number.get(step["step_number"], {})
            rollback_params = {
                **prior_result,
                "confirm_terminate": True,  # rollback implies confirmation
            }

            # Recover asset_ids from the stored execution result (_asset_ids is set by
            # agent-based executors like apply_sysctl_hardening during forward execution).
            step_asset_ids = prior_result.get("_asset_ids") or []

            try:
                result = await execute_action(
                    rollback_connector, rollback_action, rollback_params, step_asset_ids,
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


# Change types that trigger asset re-sync after completion.
# Maps change_type → list of (connector_type, discovery_action_id) pairs.
# When multiple connectors are possible (e.g. identity via Okta or AD),
# we iterate and run whichever connector is present in the org.
_CHANGE_TYPE_DISCOVERY: dict[str, list[tuple[str, str]]] = {
    # AWS EC2
    "ec2_launch":      [("aws", "discover_ec2_instances")],
    "ec2_terminate":   [("aws", "discover_ec2_instances")],
    "ec2_stop":        [("aws", "discover_ec2_instances")],
    "ec2_start":       [("aws", "discover_ec2_instances")],
    "ec2_stop_start":  [("aws", "discover_ec2_instances")],
    # AWS other
    "s3_block_public_access": [("aws", "discover_s3_buckets")],
    "promote_db_replica":     [("aws", "discover_rds_instances")],
    # Identity (try okta then active_directory — whichever connector is attached)
    "offboard_user":    [("okta", "discover_users"), ("active_directory", "discover_identities")],
    "onboard_user":     [("okta", "discover_users"), ("active_directory", "discover_identities")],
    "lockdown_account": [("okta", "discover_users"), ("active_directory", "discover_identities")],
    # DNS
    "dns_update":  [("cloudflare", "discover_dns_records")],
    "dr_failover": [("cloudflare", "discover_dns_records")],
    # Endpoint
    "isolate_host": [("crowdstrike", "discover_endpoints")],
}


async def activity_post_completion_discovery(change_request_id: str) -> None:
    """Re-run discovery after stateful changes so inventory reflects reality."""
    from app.services.ingest_service import IngestService
    from app.connectors.catalog_service import get_catalog_service

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == uuid.UUID(change_request_id))
            .options(selectinload(ChangeRequest.change_plan))
        )
        cr = result.scalar_one_or_none()
        if not cr:
            return

        candidates = _CHANGE_TYPE_DISCOVERY.get(cr.change_type.value)
        if not candidates:
            return

        # Collect connector IDs referenced in plan steps
        plan = cr.change_plan
        step_connector_ids: list[str] = []
        if plan:
            for step in plan.generated_steps:
                if step.get("connector_id") and step["connector_id"] not in step_connector_ids:
                    step_connector_ids.append(step["connector_id"])

        catalog = get_catalog_service()
        service = IngestService(catalog)

        for connector_type, action_id in candidates:
            # Find a matching connector: prefer one used in the plan steps
            connector = None
            for cid in step_connector_ids:
                conn_result = await db.execute(
                    select(Connector).where(
                        Connector.id == uuid.UUID(cid),
                        Connector.connector_type == connector_type,
                    )
                )
                connector = conn_result.scalar_one_or_none()
                if connector:
                    break

            # Fall back: any connector of that type in the org
            if not connector:
                conn_result = await db.execute(
                    select(Connector).where(
                        Connector.organization_id == cr.organization_id,
                        Connector.connector_type == connector_type,
                    )
                )
                connector = conn_result.scalars().first()

            if not connector:
                logger.info(
                    "No %s connector found for post-completion discovery on %s",
                    connector_type, change_request_id,
                )
                continue

            # Verify the action is an ingest action before running
            try:
                action_def = catalog.get_action_def(connector_type, action_id)
                if action_def.get("action_type") != "ingest":
                    continue
            except KeyError:
                logger.warning(
                    "Discovery action %s not found in %s catalog",
                    action_id, connector_type,
                )
                continue

            try:
                await service.run(action_id, connector, cr.organization_id, db)
                await db.commit()
                logger.info(
                    "Post-completion discovery %s/%s ran for CR %s",
                    connector_type, action_id, change_request_id,
                )
            except Exception as exc:
                logger.warning(
                    "Post-completion discovery %s/%s failed for CR %s: %s",
                    connector_type, action_id, change_request_id, exc,
                )


async def activity_write_appdiscovery_metadata(
    change_request_id: str,
    asset_ids: list[str],
    execution_result: dict,
) -> None:
    """Write discovered applications to asset_metadata after agent_appdiscovery CR completes."""
    from app.services.app_discovery_service import write_discovered_apps_to_metadata
    async with AsyncSessionLocal() as db:
        await write_discovered_apps_to_metadata(db, asset_ids, execution_result)
