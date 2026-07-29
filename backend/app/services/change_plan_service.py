# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Shared plan-generation logic used by both the change_requests router
and the runbook CR bridge.
"""
import asyncio
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request

log = logging.getLogger(__name__)

_OFFBOARD_DISCOVERY_TYPES = {
    "active_directory", "okta", "entra_id", "google_workspace",
    "github", "slack", "crowdstrike",
}


async def _discover_account_on_connector(parameters: dict, connector) -> dict:
    """Call discover_accounts.execute for a single connector. Isolated for testability."""
    from app.connectors.executors.offboard_user.steps import discover_accounts
    return await discover_accounts.execute(parameters, connector)


async def _run_offboard_discovery(db: AsyncSession, target_email: str, organization_id) -> list[dict]:
    """Query all identity connectors in the org and discover whether target_email has an account."""
    from app.models.connector import Connector, ConnectorType

    result = await db.execute(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.connector_type.in_([
                ConnectorType(t) for t in _OFFBOARD_DISCOVERY_TYPES
                if t in [e.value for e in ConnectorType]
            ]),
        )
    )
    connectors = list(result.scalars().all())

    from app.services.connector_service import _attach_credentials

    connector_params = []
    for connector in connectors:
        ct = connector.connector_type.value if hasattr(connector.connector_type, "value") else str(connector.connector_type)
        await _attach_credentials(connector, db)
        connector_params.append((connector, ct))

    _DISCOVERY_TIMEOUT_S = 15  # per-connector timeout; prevents slow/dead endpoints from blocking the plan

    async def _timed_discover(params, connector):
        return await asyncio.wait_for(
            _discover_account_on_connector(params, connector),
            timeout=_DISCOVERY_TIMEOUT_S,
        )

    tasks = [
        _timed_discover({"target_email": target_email, "connector_type": ct}, connector)
        for connector, ct in connector_params
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    manifest = []
    for (connector, ct), result in zip(connector_params, results):
        if isinstance(result, Exception):
            log.warning("Discovery failed for connector %s (%s): %s", connector.id, ct, result)
            manifest.append({
                "connector_id": str(connector.id),
                "connector_type": ct,
                "found": False,
                "account_identifier": None,
                "details": {"error": str(result)},
            })
        else:
            manifest.append({
                "connector_id": str(connector.id),
                "connector_type": ct,
                "found": result.get("found", False),
                "account_identifier": result.get("account_identifier"),
                "details": result.get("details", {}),
            })

    return manifest


class PlanBlockedError(Exception):
    """Raised when the safety scorer blocks plan generation."""
    def __init__(self, blocking_issues: list):
        self.blocking_issues = blocking_issues
        super().__init__(f"Safety review blocked: {blocking_issues}")


@dataclass
class PlanResult:
    plan: "ChangePlan"
    risk_level: str
    risk_score: float
    risk_factors: list
    warnings: list
    blocking_issues: list = field(default_factory=list)


async def plan_cr(db: AsyncSession, cr: ChangeRequest) -> PlanResult:
    """
    Generate a plan for a ChangeRequest in-place.

    Sets cr.status = 'planned', creates or updates the associated ChangePlan,
    and flushes (does NOT commit — caller must commit).

    Raises PlanBlockedError if the safety scorer blocks the plan.
    The cr.change_plan relationship will be eagerly loaded if not already present.
    """
    # Refresh to ensure change_plan is loaded for callers that didn't pre-load it.
    await db.refresh(cr, ["change_plan"])

    asset_ids = [uuid.UUID(str(aid)) for aid in (cr.target_asset_ids or [])]
    assets_result = await db.execute(
        select(Asset).options(selectinload(Asset.connector), selectinload(Asset.connectors)).where(Asset.id.in_(asset_ids))
    )
    assets = list(assets_result.scalars().all())

    safety_result = score_change_request(cr, assets)

    if safety_result.is_blocked:
        raise PlanBlockedError(safety_result.blocking_issues)

    # offboard_user: run discovery before plan generation, build plan directly
    if cr.change_type == ChangeType.offboard_user:
        desired = cr.desired_outcome or {}
        target_email = desired.get("target_email")
        if not target_email:
            raise PlanBlockedError(["offboard_user requires 'target_email' in desired_outcome"])

        discovery_manifest = await _run_offboard_discovery(db, target_email, cr.organization_id)
        discovered = [r for r in discovery_manifest if r.get("found")]

        if not discovered:
            n = len(discovery_manifest)
            raise PlanBlockedError([
                f"No accounts found for {target_email} across {n} connected system(s). "
                "Ensure identity connectors are configured and credentials are valid."
            ])

        # Resolved connectors for build_plan — only found ones, with account_identifier
        resolved_connectors = [
            {
                "connector_id": r["connector_id"],
                "connector_type": r["connector_type"],
                "asset_id": None,
                "account_identifier": r["account_identifier"],
            }
            for r in discovered
        ]

        # Inject discovery manifest so report step can include it
        payload = {**desired, "_discovery_manifest": discovery_manifest}

        from app.connectors.executors.offboard_user import build_plan as _offboard_build_plan
        from app.services.planning_engine import ChangePlanData, _calculate_blast_radius

        generated_steps = await _offboard_build_plan(payload, resolved_connectors)

        plan_data = ChangePlanData(
            generated_steps=generated_steps,
            preflight_checks=[],
            blast_radius=_calculate_blast_radius(cr, assets, safety_result, steps=generated_steps),
            rollback_plan={
                "rollback_capability": "full",
                "filo_order": "phases 4→3→2→1; phases 5 and 6 have no rollback",
            },
            verification_plan={"phase": 5, "checks": ["account_disabled_per_connector"]},
        )

        if cr.change_plan:
            plan = cr.change_plan
            plan.generated_steps = plan_data.generated_steps
            plan.preflight_checks = plan_data.preflight_checks
            plan.blast_radius = plan_data.blast_radius
            plan.rollback_plan = plan_data.rollback_plan
            plan.verification_plan = plan_data.verification_plan
        else:
            plan = ChangePlan(
                change_request_id=cr.id,
                generated_steps=plan_data.generated_steps,
                preflight_checks=plan_data.preflight_checks,
                blast_radius=plan_data.blast_radius,
                rollback_plan=plan_data.rollback_plan,
                verification_plan=plan_data.verification_plan,
                generated_by=PlanGeneratedBy.system,
            )
            db.add(plan)

        cr.risk_level = safety_result.risk_level
        cr.status = ChangeRequestStatus.planned
        cr.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return PlanResult(
            plan=plan,
            risk_level=safety_result.risk_level.value,
            risk_score=float(safety_result.risk_score),
            risk_factors=[f.name for f in safety_result.risk_factors],
            warnings=list(safety_result.warnings or []),
            blocking_issues=[],
        )

    plan_data = generate_plan(cr, assets, safety_result)

    if cr.change_plan:
        plan = cr.change_plan
        plan.generated_steps = plan_data.generated_steps
        plan.preflight_checks = plan_data.preflight_checks
        plan.blast_radius = plan_data.blast_radius
        plan.rollback_plan = plan_data.rollback_plan
        plan.verification_plan = plan_data.verification_plan
    else:
        plan = ChangePlan(
            change_request_id=cr.id,
            generated_steps=plan_data.generated_steps,
            preflight_checks=plan_data.preflight_checks,
            blast_radius=plan_data.blast_radius,
            rollback_plan=plan_data.rollback_plan,
            verification_plan=plan_data.verification_plan,
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan)

    cr.risk_level = safety_result.risk_level
    cr.status = ChangeRequestStatus.planned
    cr.updated_at = datetime.now(timezone.utc)

    await db.flush()
    return PlanResult(
        plan=plan,
        risk_level=safety_result.risk_level.value,
        risk_score=float(safety_result.risk_score),
        risk_factors=[f.name for f in safety_result.risk_factors],
        warnings=list(safety_result.warnings or []),
        blocking_issues=[],
    )
