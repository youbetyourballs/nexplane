# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor for credential_rotation_fanout CR type.

Phases: rotate (optional) → scan → update (FILO stack) → verify → paused on failure.
Rollback: FILO unwind via per-surface rollback functions.
"""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)

# --- Surface routing ---

_AWS_SCAN_SURFACES = {"aws_lambda_env", "aws_ecs_task_def_env", "aws_ssm_parameter"}
_K8S_SCAN_SURFACES = {"k8s_configmap", "k8s_deployment_env"}


async def _load_connector_by_type(connector_type: str, organization_id, db):
    from sqlalchemy import select
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials
    res = await db.execute(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.connector_type == ConnectorType(connector_type),
        )
    )
    conns = res.scalars().all()
    if not conns:
        return None
    # Prefer connectors that have credentials — attach and return first with non-empty creds.
    first = None
    for conn in conns:
        await _attach_credentials(conn, db)
        if conn.credentials:
            return conn
        if first is None:
            first = conn
    return first


async def _build_scan_proxy(cr_id, org_id, search_terms: list, region=None) -> object:
    """Build a lightweight CR proxy for scan functions."""
    params = {"search_terms": search_terms}
    if region:
        params["region"] = region
    return type("_ScanCR", (), {
        "parameters": params,
        "desired_outcome": params,
        "organization_id": org_id,
        "id": cr_id,
    })()


async def _run_scan_for_scope(scope: str, search_terms: list, cr_id, org_id, db) -> list:
    """Fan out scan functions for a given scope, return list of hits."""
    connector = await _load_connector_by_type(scope, org_id, db)
    if not connector:
        logger.warning("No %s connector found for org %s, skipping scan", scope, org_id)
        return []
    proxy = await _build_scan_proxy(cr_id, org_id, search_terms)
    hits = []
    if scope == "aws":
        from app.connectors.executors.aws.reference_scan import (
            scan_lambda_env_vars, scan_ecs_task_defs, scan_ssm_parameters_metadata,
        )
        for fn in [scan_lambda_env_vars, scan_ecs_task_defs, scan_ssm_parameters_metadata]:
            try:
                r = await fn(proxy, connector, db)
                hits.extend(r.get("hits", []))
            except Exception as exc:
                logger.warning("AWS scan %s failed: %s", fn.__name__, exc)
    elif scope == "kubernetes":
        from app.connectors.executors.kubernetes.reference_scan import (
            scan_configmaps, scan_deployment_env,
        )
        for fn in [scan_configmaps, scan_deployment_env]:
            try:
                r = await fn(proxy, connector, db)
                hits.extend(r.get("hits", []))
            except Exception as exc:
                logger.warning("K8s scan %s failed: %s", fn.__name__, exc)
    return hits


async def _update_consumer(consumer: dict, new_value: str, db, connector_cache: dict) -> dict:
    """Call the appropriate update function for a consumer. Returns update_result."""
    surface = consumer["surface"]
    surface_meta = consumer.get("surface_metadata", {})
    old_value = consumer["matched_term"]
    connector_type = "aws" if surface in _AWS_SCAN_SURFACES else "kubernetes"
    org_id = consumer["_org_id"]

    if connector_type not in connector_cache:
        connector_cache[connector_type] = await _load_connector_by_type(connector_type, org_id, db)
    connector = connector_cache[connector_type]
    if not connector:
        return {"status": "error", "error": f"No {connector_type} connector found"}

    params = {**surface_meta, "old_value": old_value, "new_value": new_value}
    proxy = type("_UpdateCR", (), {"parameters": params})()

    try:
        if surface == "aws_lambda_env":
            from app.connectors.executors.aws.reference_update import update_lambda_env_var
            result = await update_lambda_env_var(proxy, connector, db)
        elif surface == "aws_ecs_task_def_env":
            from app.connectors.executors.aws.reference_update import update_ecs_task_def_env
            result = await update_ecs_task_def_env(proxy, connector, db)
        elif surface == "aws_ssm_parameter":
            from app.connectors.executors.aws.reference_update import update_ssm_parameter_value
            result = await update_ssm_parameter_value(proxy, connector, db)
        elif surface == "k8s_configmap":
            from app.connectors.executors.kubernetes.reference_update import update_configmap_value
            result = await update_configmap_value(proxy, connector, db)
        elif surface == "k8s_deployment_env":
            from app.connectors.executors.kubernetes.reference_update import update_deployment_env_var
            result = await update_deployment_env_var(proxy, connector, db)
        else:
            result = {"status": "skipped", "reason": f"unknown surface: {surface}"}
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}

    return result


async def _rollback_consumer(consumer: dict, db, connector_cache: dict) -> dict:
    """Reverse a consumer update using rollback_data from the update_result."""
    surface = consumer["surface"]
    update_result = consumer.get("update_result", {})
    rollback_data = update_result.get("rollback_data", {})
    if not rollback_data:
        return {"rolled_back": False, "reason": "no rollback_data"}

    connector_type = "aws" if surface in _AWS_SCAN_SURFACES else "kubernetes"
    org_id = consumer["_org_id"]
    if connector_type not in connector_cache:
        connector_cache[connector_type] = await _load_connector_by_type(connector_type, org_id, db)
    connector = connector_cache[connector_type]
    if not connector:
        return {"rolled_back": False, "error": f"No {connector_type} connector found"}

    rollback_proxy = type("_RbCR", (), {
        "parameters": consumer.get("update_params", {}),
        "execution_result": {"rollback_data": rollback_data},
    })()

    try:
        if surface == "aws_lambda_env":
            from app.connectors.executors.aws.reference_update import rollback_lambda_env_var
            result = await rollback_lambda_env_var(rollback_proxy, connector, db)
        elif surface == "aws_ecs_task_def_env":
            from app.connectors.executors.aws.reference_update import rollback_ecs_task_def_env
            result = await rollback_ecs_task_def_env(rollback_proxy, connector, db)
        elif surface == "aws_ssm_parameter":
            from app.connectors.executors.aws.reference_update import rollback_ssm_parameter_value
            result = await rollback_ssm_parameter_value(rollback_proxy, connector, db)
        elif surface == "k8s_configmap":
            from app.connectors.executors.kubernetes.reference_update import rollback_configmap_value
            result = await rollback_configmap_value(rollback_proxy, connector, db)
        elif surface == "k8s_deployment_env":
            from app.connectors.executors.kubernetes.reference_update import rollback_deployment_env_var
            result = await rollback_deployment_env_var(rollback_proxy, connector, db)
        else:
            result = {"rolled_back": False, "reason": f"unknown surface: {surface}"}
    except Exception as exc:
        result = {"rolled_back": False, "error": str(exc)}

    # Normalize: rollback functions return {"status": "rolled_back"|"success"|...}
    # Map to rolled_back bool for uniform upstream handling.
    if isinstance(result, dict) and "rolled_back" not in result:
        status = result.get("status", "")
        result["rolled_back"] = status in ("rolled_back", "success")

    return result


async def _persist(run: ExecutionRun, data: dict) -> None:
    run.result = data


def _build_result(consumers: list, rotation_result: dict, phase: str, paused: bool = False) -> dict:
    return {
        "phase": phase,
        "rotation_result": rotation_result,
        "consumers": consumers,
        "paused": paused,
        "has_warnings": any(
            (c.get("update_result") or {}).get("status") not in (None, "updated", "skipped")
            for c in consumers
        ),
    }


async def execute_credential_rotation_fanout(cr_id: uuid.UUID) -> dict:
    """Main executor for credential_rotation_fanout CRs.

    Phases:
    1. Rotate (optional): call the specified rotate action
    2. Scan: find all consumers with old value
    3. Update: push new value to each consumer (serial, FILO stack order)
    4. Verify: re-scan; paused if any consumers still have old value
    """
    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()
        org_id = cr.organization_id
        desired = cr.desired_outcome or {}

        search_terms = desired.get("search_terms", [])
        new_value = desired.get("new_value", "")
        scan_scope = desired.get("scan_scope", [])
        rotate_spec = desired.get("rotate")
        verify_timeout = desired.get("verify_timeout_seconds", 30)

        run_res = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.change_request_id == cr_id,
                ExecutionRun.status == ExecutionStatus.running,
            ).order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        run = run_res.scalar_one_or_none()
        if not run:
            run = ExecutionRun(
                id=uuid.uuid4(),
                change_request_id=cr_id,
                workflow_id=f"cred-fanout-{cr_id}",
                status=ExecutionStatus.running,
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.flush()

        prior = run.result or {}
        rotation_result = prior.get("rotation_result", {})
        consumers = prior.get("consumers", [])
        phase = prior.get("phase", "")

        # --- Phase 1: Rotate (optional) ---
        if rotate_spec and phase not in ("scan", "update", "verify"):
            connector = await _load_connector_by_type(rotate_spec["connector_type"], org_id, db)
            try:
                from app.services.connector_service import execute_action
                rot_result = await execute_action(
                    rotate_spec["connector_type"],
                    rotate_spec["action_id"],
                    rotate_spec.get("params", {}),
                    [],
                    connector=connector,
                    db=db,
                )
                rotation_result = rot_result
                # If rotate action returned new_value, use it
                if rot_result.get("new_value"):
                    new_value = rot_result["new_value"]
                    desired = {**desired, "new_value": new_value}
                    cr.desired_outcome = desired
            except Exception as exc:
                rotation_result = {"error": str(exc), "success": False}
            phase = "scan"
            await _persist(run, _build_result(consumers, rotation_result, phase))
            await db.commit()

        # --- Phase 2: Scan ---
        if phase in ("", "scan"):
            all_hits = []
            for scope in scan_scope:
                hits = await _run_scan_for_scope(scope, search_terms, cr_id, org_id, db)
                all_hits.extend(hits)
            # Build consumers list with index
            consumers = []
            for i, hit in enumerate(all_hits):
                consumers.append({
                    "index": i,
                    "surface": hit.get("surface", ""),
                    "location": hit.get("location", ""),
                    "matched_term": hit.get("matched_term", ""),
                    "surface_metadata": hit.get("consumer_identity", {}).get("surface_metadata", {}),
                    "_org_id": str(org_id),
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
            phase = "update"
            await _persist(run, _build_result(consumers, rotation_result, phase))
            await db.commit()

        # --- Phase 3: Update ---
        if phase == "update":
            pending = [c for c in consumers if not c.get("update_result")]
            connector_cache: dict = {}
            for consumer in pending:
                result = await _update_consumer(consumer, new_value, db, connector_cache)
                consumer["update_result"] = result
                consumer["update_params"] = {
                    **consumer.get("surface_metadata", {}),
                    "old_value": consumer["matched_term"],
                    "new_value": new_value,
                }
            # Check for failures
            failed = [c for c in consumers if c.get("update_result", {}).get("status") == "error"]
            if failed:
                await _persist(run, _build_result(consumers, rotation_result, "update", paused=True))
                await db.commit()
                cr.status = ChangeRequestStatus.paused
                cr.updated_at = datetime.now(timezone.utc)
                await db.commit()
                return _build_result(consumers, rotation_result, "update", paused=True)
            phase = "verify"
            await _persist(run, _build_result(consumers, rotation_result, phase))
            await db.commit()

        # --- Phase 4: Verify ---
        if phase == "verify":
            import asyncio
            if verify_timeout > 0:
                await asyncio.sleep(min(verify_timeout, 5))  # cap sleep at 5s; real wait handled by smoke polling
            # Re-scan to confirm no old values remain
            remaining_hits = []
            for scope in scan_scope:
                hits = await _run_scan_for_scope(scope, search_terms, cr_id, org_id, db)
                remaining_hits.extend(hits)
            # Match remaining hits back to consumers by location
            remaining_locs = {h.get("location", "") for h in remaining_hits}
            for consumer in consumers:
                if consumer.get("verify_result") is None:
                    still_present = consumer["location"] in remaining_locs
                    consumer["verify_result"] = {
                        "success": not still_present,
                        "still_present": still_present,
                    }
            failed_verify = [c for c in consumers if not c.get("verify_result", {}).get("success", True)]
            if failed_verify:
                result = _build_result(consumers, rotation_result, "verify", paused=True)
                await _persist(run, result)
                await db.commit()
                cr.status = ChangeRequestStatus.paused
                cr.updated_at = datetime.now(timezone.utc)
                await db.commit()
                return result
            result = _build_result(consumers, rotation_result, "completed")
            await _persist(run, result)
            run.status = ExecutionStatus.completed
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return result

        return _build_result(consumers, rotation_result, phase)


async def execute_fanout_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict:
    """FILO rollback for credential_rotation_fanout. Unwinds consumers in reverse order."""
    consumers = list(execution_result.get("consumers", []))
    consumers_reversed = list(reversed(consumers))

    connector_cache: dict = {}
    async with AsyncSessionLocal() as db:
        warnings = False
        for consumer in consumers_reversed:
            update_result = consumer.get("update_result", {})
            if not update_result or update_result.get("status") not in ("updated",):
                consumer["rollback_result"] = {"rolled_back": True, "note": "not updated, nothing to roll back"}
                continue
            result = await _rollback_consumer(consumer, db, connector_cache)
            consumer["rollback_result"] = result
            if not result.get("rolled_back", False):
                warnings = True

    return {
        "consumers": consumers,
        "rotation_result": execution_result.get("rotation_result", {}),
        "has_warnings": warnings,
    }
