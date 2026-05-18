"""
Shared plan-generation logic used by both the change_requests router
and the runbook CR bridge.
"""
import uuid
import logging
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


async def plan_cr(db: AsyncSession, cr: ChangeRequest) -> ChangePlan:
    """
    Generate a plan for a ChangeRequest in-place.

    Sets cr.status = 'planned', creates or updates the associated ChangePlan,
    and flushes (does NOT commit — caller must commit).

    Raises ValueError if the safety scorer blocks the plan.
    """
    # Eagerly load the change_plan relationship to avoid lazy-load in async context
    await db.refresh(cr, ["change_plan"])

    asset_ids = [uuid.UUID(str(aid)) for aid in (cr.target_asset_ids or [])]
    assets_result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
    )
    assets = list(assets_result.scalars().all())

    safety_result = score_change_request(cr, assets)

    if safety_result.is_blocked:
        raise ValueError(
            f"Safety review blocked plan generation: {safety_result.blocking_issues}"
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
    return plan
