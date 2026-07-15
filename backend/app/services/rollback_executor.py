# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Shared CR rollback execution. Used by manual_rollback endpoint and project rollback service."""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # still used by _load_cr_and_run / _executor_fallback

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)


async def _load_cr_and_run(
    cr_id: uuid.UUID, db: AsyncSession
) -> tuple[ChangeRequest, ExecutionRun | None, object]:
    """Load CR, its most recent completed execution run, and its change plan."""
    from app.models.change_plan import ChangePlan

    cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = cr_res.scalar_one()

    run_res = await db.execute(
        select(ExecutionRun).where(
            ExecutionRun.change_request_id == cr.id,
            ExecutionRun.status == ExecutionStatus.completed,
        ).order_by(ExecutionRun.started_at.desc()).limit(1)
    )
    latest_run = run_res.scalar_one_or_none()
    if not latest_run:
        fallback_res = await db.execute(
            select(ExecutionRun).where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        latest_run = fallback_res.scalar_one_or_none()

    plan_res = await db.execute(select(ChangePlan).where(ChangePlan.change_request_id == cr.id))
    plan = plan_res.scalar_one_or_none()
    return cr, latest_run, plan


async def _executor_fallback(
    cr: ChangeRequest, execution_result: dict, db: AsyncSession
) -> dict:
    """Call executor module's rollback() when no plan rollback steps are defined.
    Extracted from the _do_rollback closure in change_requests.py.
    """
    try:
        from app.connectors.catalog_service import get_catalog_service
        from app.models.connector import Connector as _Connector
        from app.services.connector_service import _attach_credentials

        _ct = cr.change_type.value if hasattr(cr.change_type, "value") else str(cr.change_type)
        _catalog = get_catalog_service()

        # Derive connector_type from the execution_result steps (stored at execute time).
        _exec_steps_for_ct = (
            execution_result.get("execution", {}).get("steps")
            or execution_result.get("steps")
            or []
        )
        _connector_type_from_result = next(
            (s.get("connector_type") for s in _exec_steps_for_ct if s.get("connector_type")),
            None,
        )
        # For catalog_action CRs, _ct is "catalog_action" which doesn't map to any
        # executor module. Use the action_id from the execution steps instead so the
        # executor's native rollback() is found correctly.
        if _ct == "catalog_action":
            _action_id_from_step = next(
                (s.get("action_id") for s in _exec_steps_for_ct if s.get("action_id")),
                None,
            )
            if _action_id_from_step:
                _ct = _action_id_from_step
        _connector = None
        # Resolve connector object for credentials if a connector_id is in the step
        _step_connector_id = next(
            (s.get("connector_id") for s in _exec_steps_for_ct if s.get("connector_id")),
            None,
        )
        try:
            from sqlalchemy import select as _sa_select
            async with AsyncSessionLocal() as _rdb:
                if _step_connector_id:
                    _conn_obj = await _rdb.get(_Connector, uuid.UUID(str(_step_connector_id)))
                    if _conn_obj:
                        await _attach_credentials(_conn_obj, _rdb)
                        _connector = _conn_obj
                # If no connector found via step_id, find one by type in the org
                if not _connector and _connector_type_from_result:
                    from app.models.connector import ConnectorType as _ConnectorType
                    _res = await _rdb.execute(
                        _sa_select(_Connector).where(
                            _Connector.organization_id == cr.organization_id,
                            _Connector.connector_type == _ConnectorType(_connector_type_from_result),
                        ).limit(1)
                    )
                    _conn_obj = _res.scalar_one_or_none()
                    if _conn_obj:
                        await _attach_credentials(_conn_obj, _rdb)
                        _connector = _conn_obj
        except Exception as exc:
            logger.warning("Could not load connector for rollback: %s", exc)

        # Try connector type from result first, then fall back to common types
        _mod = None
        _conn_types_to_try = []
        if _connector_type_from_result:
            _conn_types_to_try.append(_connector_type_from_result)
        _conn_types_to_try.extend(
            t for t in ("nexplane_agent", "aws", "azure_ad", "okta")
            if t != _connector_type_from_result
        )
        for _conn_type in _conn_types_to_try:
            try:
                _mod = _catalog.get_executor(_conn_type, _ct)
                break
            except Exception:
                continue

        if _mod and hasattr(_mod, "rollback"):
            # Extract step 1 result from nested execution structure so
            # rollback() receives the actual step result dict, not the
            # full workflow result envelope.
            _exec_steps = (
                execution_result.get("execution", {}).get("steps")
                or execution_result.get("steps")
                or []
            )
            _step1 = next(
                (s.get("result", {}) for s in _exec_steps if s.get("step_number") == 1),
                execution_result,
            )
            rollback_result = await _mod.rollback(
                cr.desired_outcome or {}, _step1, _connector
            )
            # Executor ran and explicitly declared nothing to undo — tag so the
            # status logic can distinguish this from "no executor found" or an exception.
            if isinstance(rollback_result, dict) and rollback_result.get("rolled_back") is False:
                rollback_result["_rollback_no_op"] = True
        else:
            rollback_result = {"rolled_back": False, "reason": "no_rollback_function_found"}
    except Exception as _exc:
        rollback_result = {"rolled_back": False, "reason": str(_exc)}

    return rollback_result


def _determine_rollback_status(
    step_results: list[dict],
    rollback_ran: bool,
) -> "ChangeRequestStatus":
    """Return truthful terminal rollback status from per-step outcomes."""
    if not rollback_ran or not step_results:
        return ChangeRequestStatus.rollback_failed
    failed = [s for s in step_results if not s.get("success", False)]
    succeeded = [s for s in step_results if s.get("success", False)]
    if not failed:
        return ChangeRequestStatus.rolled_back
    if succeeded:
        return ChangeRequestStatus.rollback_partial
    return ChangeRequestStatus.rollback_failed


async def execute_cr_rollback(
    cr_id: uuid.UUID,
    extra_execution_result: dict | None = None,
) -> dict:
    """Execute rollback for a single CR. Returns rollback result dict.

    Opens its own DB session so callers are never coupled to this function's commit.

    extra_execution_result: merged into execution_result before rollback (used for
    reconstitution — pass the backup CR's execution result here).
    """
    async with AsyncSessionLocal() as db:
        cr, latest_run, plan = await _load_cr_and_run(cr_id, db)

        execution_result = (latest_run.result or {}) if latest_run else {}
        if extra_execution_result:
            execution_result = {**execution_result, **extra_execution_result}

        steps = (plan.generated_steps if plan else []) or []
        has_rollback_steps = any(s.get("rollback_action") or s.get("rollback_action_id") for s in steps)

        if has_rollback_steps:
            from app.workflows.activities import activity_execute_rollback
            result = await activity_execute_rollback(str(cr.id), steps, execution_result)
        else:
            result = await _executor_fallback(cr, execution_result, db)

        # Determine truthful terminal state from step outcomes
        rollback_ran = True
        if isinstance(result, dict):
            # Mirror _executor_fallback's dual-key step extraction.
            # activity_execute_rollback returns {"rollback_steps": [...]} while
            # _executor_fallback returns {"steps": [...]} or {"execution": {"steps": [...]}}.
            step_results = (
                result.get("execution", {}).get("steps")
                or result.get("steps")
                or []
            )
            # Convert rollback_steps format to success-keyed format for _determine_rollback_status.
            if not step_results and isinstance(result.get("rollback_steps"), list):
                rollback_steps_raw = result["rollback_steps"]
                if rollback_steps_raw:
                    step_results = [
                        {"success": "error" not in s.get("result", {})}
                        for s in rollback_steps_raw
                    ]
                else:
                    # No steps to roll back — treat as successful (nothing to undo).
                    step_results = [{"success": True}]
            if result.get("rolled_back") is True and not step_results:
                # _executor_fallback returned direct {"rolled_back": True} — count as success.
                step_results = [{"success": True}]
            elif result.get("rolled_back") is False and not step_results:
                if result.get("_rollback_no_op"):
                    # Executor ran and decided nothing to undo — count as success.
                    step_results = [{"success": True}]
                else:
                    rollback_ran = False
        else:
            step_results = []
        cr.status = _determine_rollback_status(step_results, rollback_ran)
        cr.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return result
