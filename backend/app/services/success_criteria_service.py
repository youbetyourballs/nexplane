# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Evaluates project success criteria against live platform state."""

import datetime
import uuid as _uuid
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_success_criteria import (
    ProjectSuccessCriteria,
    CriteriaType,
    CriteriaResult,
)
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services import host_intelligence_service as _his


def _mock_user(org_id):
    """Minimal user-like object with organization_id for host_intelligence_service."""
    return SimpleNamespace(organization_id=org_id)


async def evaluate_criterion(
    db: AsyncSession,
    criterion: ProjectSuccessCriteria,
    org_id,
) -> tuple[CriteriaResult, str]:
    """Evaluate a single criterion. Returns (result, detail_message)."""

    if criterion.type == CriteriaType.cr_completed:
        cr_id = criterion.assertion.get("cr_id")
        if not cr_id:
            return CriteriaResult.fail, "assertion missing cr_id"
        try:
            cr_uuid = _uuid.UUID(str(cr_id))
        except ValueError:
            return CriteriaResult.fail, f"assertion cr_id is not a valid UUID: {cr_id}"
        cr = await db.get(ChangeRequest, cr_uuid)
        if not cr:
            return CriteriaResult.fail, f"CR {cr_id} not found"
        if cr.status == ChangeRequestStatus.completed:
            return CriteriaResult.pass_, f"CR '{cr.title}' completed"
        status_val = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
        return CriteriaResult.fail, f"CR '{cr.title}' status={status_val}"

    elif criterion.type == CriteriaType.host_state_check:
        asset_id = criterion.assertion.get("asset_id")
        field = criterion.assertion.get("field")
        operator = criterion.assertion.get("operator", "eq")
        expected = criterion.assertion.get("value")
        if not all([asset_id, field, expected is not None]):
            return CriteriaResult.fail, "assertion missing asset_id/field/value"
        try:
            tool = criterion.assertion.get("tool", "deep_discover")
            intel = await _his.run_intelligence_tool(
                db=db,
                user=_mock_user(org_id),
                asset_id=asset_id,
                tool_name=tool,
                command=tool,
                params={},
            )
            if intel is None:
                return CriteriaResult.fail, f"no intelligence data for asset {asset_id}"
            actual = intel.get(field)
            passed = _apply_operator(actual, operator, expected)
            if passed:
                return CriteriaResult.pass_, f"{field}={actual} matches {operator} {expected}"
            return CriteriaResult.fail, f"{field}={actual} does not match {operator} {expected}"
        except Exception as exc:
            return CriteriaResult.fail, f"host_state_check error: {exc}"

    elif criterion.type == CriteriaType.service_check:
        asset_id = criterion.assertion.get("asset_id")
        service_name = criterion.assertion.get("service")
        expected_state = criterion.assertion.get("state", "running")
        if not asset_id or not service_name:
            return CriteriaResult.fail, "assertion missing asset_id or service"
        try:
            intel = await _his.run_intelligence_tool(
                db=db,
                user=_mock_user(org_id),
                asset_id=asset_id,
                tool_name="deep_discover",
                command="deep_discover",
                params={},
            )
            if intel is None:
                return CriteriaResult.fail, f"no intelligence data for asset {asset_id}"
            services = intel.get("running_services", [])
            match = next((s for s in services if s.get("name") == service_name), None)
            if match is None:
                return CriteriaResult.fail, f"service {service_name} not found in host intel"
            actual_state = match.get("state", "unknown")
            if actual_state == expected_state:
                return CriteriaResult.pass_, f"service {service_name} state={actual_state}"
            return (
                CriteriaResult.fail,
                f"service {service_name} state={actual_state}, expected {expected_state}",
            )
        except Exception as exc:
            return CriteriaResult.fail, f"service_check error: {exc}"

    elif criterion.type == CriteriaType.port_check:
        asset_id = criterion.assertion.get("asset_id")
        port = criterion.assertion.get("port")
        expected_open = criterion.assertion.get("open", True)
        if not asset_id or port is None:
            return CriteriaResult.fail, "assertion missing asset_id or port"
        try:
            intel = await _his.run_intelligence_tool(
                db=db,
                user=_mock_user(org_id),
                asset_id=asset_id,
                tool_name="deep_discover",
                command="deep_discover",
                params={},
            )
            if intel is None:
                return CriteriaResult.fail, f"no intelligence data for asset {asset_id}"
            open_ports = intel.get("open_ports", [])
            port_open = int(port) in [int(p) for p in open_ports]
            if port_open == expected_open:
                state = "open" if port_open else "closed"
                return CriteriaResult.pass_, f"port {port} is {state} as expected"
            state = "open" if port_open else "closed"
            expected_label = "open" if expected_open else "closed"
            return CriteriaResult.fail, f"port {port} is {state}, expected {expected_label}"
        except Exception as exc:
            return CriteriaResult.fail, f"port_check error: {exc}"

    elif criterion.type == CriteriaType.manual:
        instructions = criterion.assertion.get(
            "instructions", "Manual verification required."
        )
        return CriteriaResult.pending_manual, instructions

    return CriteriaResult.fail, f"unknown criterion type: {criterion.type}"


def _apply_operator(actual, operator: str, expected) -> bool:
    if operator == "eq":
        return actual == expected
    elif operator == "neq":
        return actual != expected
    elif operator == "contains":
        return expected in (actual or "")
    elif operator == "gt":
        return float(actual) > float(expected)
    elif operator == "lt":
        return float(actual) < float(expected)
    return False


async def evaluate_all_criteria(
    db: AsyncSession,
    project_id,
    org_id,
) -> dict:
    """Evaluate all criteria for a project. Updates last_result in DB. Returns summary dict."""
    result = await db.execute(
        select(ProjectSuccessCriteria).where(
            ProjectSuccessCriteria.project_id == project_id
        )
    )
    criteria = result.scalars().all()

    if not criteria:
        return {"overall": "not_checked", "criteria": []}

    results = []
    now = datetime.datetime.utcnow()
    for c in criteria:
        res, detail = await evaluate_criterion(db, c, org_id)
        c.last_checked_at = now
        c.last_result = res
        c.last_result_detail = detail
        db.add(c)
        results.append(
            {
                "id": str(c.id),
                "type": c.type.value,
                "description": c.description,
                "result": res.value,
                "detail": detail,
                "last_checked_at": now.isoformat(),
            }
        )
    await db.commit()

    result_values = [r["result"] for r in results]
    if all(v == "pass" for v in result_values):
        overall = "pass"
    elif any(v == "fail" for v in result_values):
        overall = "fail"
    elif any(v == "pending_manual" for v in result_values):
        overall = "partial"
    else:
        overall = "not_checked"

    return {"overall": overall, "criteria": results}
