# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Serial execution engine for credential_rotation CRs with FILO rollback."""
import importlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.services.connector_service import execute_action

logger = logging.getLogger(__name__)


async def _resolve_connector(connector_type: str, organization_id: uuid.UUID, db):
    """Find the most recent active connector of the given type in the org."""
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials
    try:
        ct_enum = ConnectorType(connector_type)
        res = await db.execute(
            select(Connector).where(
                Connector.organization_id == organization_id,
                Connector.connector_type == ct_enum,
            ).order_by(Connector.created_at.desc()).limit(1)
        )
        connector = res.scalar_one_or_none()
        if connector:
            await _attach_credentials(connector, db)
        return connector
    except Exception as exc:
        logger.warning("Connector lookup failed for %s: %s", connector_type, exc)
        return None


def _init_steps(desired_steps: list) -> list:
    return [
        {
            "index": i,
            "label": s.get("label", f"Step {i + 1}"),
            "connector_type": s["connector_type"],
            "action_id": s["action_id"],
            "params": s.get("params", {}),
            "status": "pending",
            "result": None,
            "error": None,
        }
        for i, s in enumerate(desired_steps)
    ]


async def execute_steps(cr_id: uuid.UUID) -> dict:
    """Execute credential rotation steps serially from the first pending step.

    Updates ExecutionRun.result after every step. Sets CR.status = paused on
    step failure and returns {"paused": True, ...}. Returns {"steps": [...]}
    when all steps complete (caller sets CR to completed).
    """
    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()

        # Find the current execution run (running or most recent)
        run_res = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr_id)
            .where(ExecutionRun.status.in_([ExecutionStatus.running, ExecutionStatus.pending]))
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        run = run_res.scalar_one_or_none()
        if run is None:
            fallback_res = await db.execute(
                select(ExecutionRun)
                .where(ExecutionRun.change_request_id == cr_id)
                .order_by(ExecutionRun.started_at.desc())
                .limit(1)
            )
            run = fallback_res.scalar_one_or_none()

        # Load or initialize step state
        current_result = (run.result or {}) if run else {}
        steps = current_result.get("steps") or _init_steps(
            (cr.desired_outcome or {}).get("steps", [])
        )

        # Find steps still needing execution
        for step in steps:
            if step["status"] not in ("pending", "executing"):
                continue

            step["status"] = "executing"
            if run is not None:
                run.result = {"steps": steps}
                await db.commit()

            try:
                connector = await _resolve_connector(
                    step["connector_type"], cr.organization_id, db
                )
                result_data = await execute_action(
                    step["connector_type"],
                    step["action_id"],
                    step["params"],
                    [],
                    connector=connector,
                    db=db if connector is not None else None,
                )
                await db.commit()
                step["status"] = "completed"
                step["result"] = result_data
            except Exception as exc:
                logger.error(
                    "credential_rotation step %d (%s.%s) failed: %s",
                    step["index"], step["connector_type"], step["action_id"], exc,
                )
                step["status"] = "failed"
                step["error"] = str(exc)
                if run is not None:
                    run.result = {"steps": steps}
                cr.status = ChangeRequestStatus.paused
                cr.updated_at = datetime.now(timezone.utc)
                await db.commit()
                return {"steps": steps, "paused": True, "current_step": step["index"]}

            if run is not None:
                run.result = {"steps": steps}
                await db.commit()

        return {"steps": steps}


async def execute_filo_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict:
    """Unwind completed steps in reverse order (FILO).

    Returns {"rollback_steps": [...], "all_rolled_back": bool}.
    Steps returning rolled_back=False are recorded as warnings; rollback continues.
    """
    from app.connectors.catalog_service import get_catalog_service

    steps = execution_result.get("steps") or execution_result.get("execution", {}).get("steps", [])
    completed = [s for s in steps if s.get("status") == "completed"]
    rollback_steps = []
    catalog = get_catalog_service()

    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()

        for step in reversed(completed):
            connector_type = step["connector_type"]
            action_id = step["action_id"]

            connector = await _resolve_connector(connector_type, cr.organization_id, db)

            # Resolve executor module
            mod = None
            try:
                action_def = catalog.get_action_def(connector_type, action_id)
                executor_ref = action_def.get("executor", "")
                parts = executor_ref.split(".")
                if len(parts) == 2:
                    mod = importlib.import_module(
                        f"app.connectors.executors.{parts[0]}.{parts[1]}"
                    )
            except Exception as exc:
                rollback_steps.append({
                    "index": step["index"],
                    "label": step.get("label", ""),
                    "rollback_result": {
                        "rolled_back": False,
                        "error": f"Cannot load executor: {exc}",
                    },
                })
                continue

            try:
                if mod is not None and hasattr(mod, "rollback"):
                    rb_result = await mod.rollback(
                        step.get("params", {}),
                        step.get("result") or {},
                        connector,
                    )
                else:
                    rb_result = {"rolled_back": False, "reason": "no_rollback_function"}
            except Exception as exc:
                rb_result = {"rolled_back": False, "error": str(exc)}

            rollback_steps.append({
                "index": step["index"],
                "label": step.get("label", ""),
                "rollback_result": rb_result,
            })

    all_ok = all(
        r["rollback_result"].get("rolled_back", False) for r in rollback_steps
    )
    return {"rollback_steps": rollback_steps, "all_rolled_back": all_ok}
