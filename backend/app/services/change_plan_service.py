# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Shared plan-generation logic used by both the change_requests router
and the runbook CR bridge.
"""
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request

log = logging.getLogger(__name__)


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
        select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
    )
    assets = list(assets_result.scalars().all())

    safety_result = score_change_request(cr, assets)

    if safety_result.is_blocked:
        raise PlanBlockedError(safety_result.blocking_issues)

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
