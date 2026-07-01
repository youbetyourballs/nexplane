# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, AsyncSessionLocal
from app.models.asset import Asset, Environment, AssetType, Criticality
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.user import User
from app.routers import current_user
from app.schemas.asset import AssetCreate, AssetRead, AssetUpdate, BulkTagOperation
from app.services.audit_service import record_event
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.workflows import runner as workflow_runner
from app.workflows.execute_change_workflow import execute_change_workflow

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.get("", response_model=list[AssetRead])
async def list_assets(
    q: str | None = Query(None, description="Asset name substring search"),
    env: str | None = Query(None, description="Filter by environment"),
    asset_type: str | None = Query(None, description="Filter by asset type"),
    criticality: str | None = Query(None, description="Filter by criticality"),
    tag: str | None = Query(None, description="Filter by tag (asset must have this tag)"),
    connector_id: uuid.UUID | None = Query(None, description="Filter by connector that discovered this asset"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).options(selectinload(Asset.connector)).where(Asset.organization_id == user.organization_id)

    if q:
        stmt = stmt.where(Asset.name.ilike(f"%{q}%"))
    if env:
        try:
            stmt = stmt.where(Asset.environment == Environment(env))
        except ValueError:
            pass
    if asset_type:
        try:
            stmt = stmt.where(Asset.asset_type == AssetType(asset_type))
        except ValueError:
            pass
    if criticality:
        try:
            stmt = stmt.where(Asset.criticality == Criticality(criticality))
        except ValueError:
            pass
    if connector_id:
        stmt = stmt.where(Asset.connector_id == connector_id)

    result = await db.execute(stmt.order_by(Asset.name))
    assets = result.scalars().all()

    # Tag filter applied in Python (JSON array containment)
    if tag:
        assets = [a for a in assets if tag in (a.tags or [])]

    return [
        AssetRead(
            **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
            connector_name=asset.connector.name if asset.connector else None,
            connector_type=asset.connector.connector_type.value if asset.connector else None,
        )
        for asset in assets
    ]


@router.get("/tags", response_model=list[str])
async def get_asset_tags(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all unique tags used across the org's assets, sorted alphabetically."""
    result = await db.execute(
        select(Asset.tags).where(Asset.organization_id == user.organization_id)
    )
    all_tags: set[str] = set()
    for (tags,) in result:
        if tags:
            all_tags.update(tags)
    return sorted(all_tags)


@router.post("", response_model=AssetRead, status_code=201)
async def create_asset(
    body: AssetCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = Asset(organization_id=user.organization_id, **body.model_dump())
    db.add(asset)
    await db.flush()
    await record_event(db, user.organization_id, "asset.created",
                       {"asset_id": str(asset.id), "name": asset.name}, actor_id=user.id)
    await db.commit()
    await db.refresh(asset)
    return AssetRead(
        **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
        connector_name=None,
        connector_type=None,
    )


@router.patch("/bulk-tag")
async def bulk_tag_assets(
    body: BulkTagOperation,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_(body.asset_ids),
            Asset.organization_id == user.organization_id,
        )
    )
    assets = result.scalars().all()

    if len(assets) != len(body.asset_ids):
        raise HTTPException(status_code=403, detail="One or more assets not found or not accessible")

    for asset in assets:
        current_tags: list[str] = asset.tags or []
        if body.operation == "add":
            new_tags = list(dict.fromkeys(current_tags + body.tags))
        elif body.operation == "remove":
            new_tags = [t for t in current_tags if t not in body.tags]
        else:  # "set"
            new_tags = list(body.tags)
        asset.tags = new_tags

    await record_event(
        db, user.organization_id, "asset.bulk_tagged",
        {"operation": body.operation, "tags": body.tags, "asset_count": len(assets)},
        actor_id=user.id,
    )
    await db.commit()
    return {"updated": len(assets)}


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(
            Asset.id == asset_id,
            Asset.organization_id == user.organization_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetRead(
        **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
        connector_name=asset.connector.name if asset.connector else None,
        connector_type=asset.connector.connector_type.value if asset.connector else None,
    )


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    await record_event(db, user.organization_id, "asset.deleted",
                       {"asset_id": str(asset.id), "name": asset.name}, actor_id=user.id)
    # Cascade-delete agent jobs and registrations before deleting the asset
    # to avoid FK violations (agent_registrations references assets).
    from sqlalchemy import select as _select, delete as _delete
    from app.models.agent import AgentRegistration, AgentJob
    reg_ids_result = await db.execute(
        _select(AgentRegistration.id).where(AgentRegistration.asset_id == asset_id)
    )
    reg_ids = [r[0] for r in reg_ids_result.fetchall()]
    if reg_ids:
        await db.execute(_delete(AgentJob).where(AgentJob.agent_registration_id.in_(reg_ids)))
        await db.execute(_delete(AgentRegistration).where(AgentRegistration.asset_id == asset_id))
    await db.delete(asset)
    await db.commit()


@router.patch("/{asset_id}", response_model=AssetRead)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    if body.name is not None:
        asset.name = body.name
    if body.criticality is not None:
        asset.criticality = body.criticality
    if body.asset_metadata is not None:
        asset.asset_metadata = body.asset_metadata
    if body.tags is not None:
        asset.tags = body.tags

    await record_event(db, user.organization_id, "asset.updated",
                       {"asset_id": str(asset.id), "changes": body.model_dump(exclude_none=True)},
                       actor_id=user.id)
    await db.commit()
    await db.refresh(asset)
    return AssetRead(
        **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
        connector_name=None,
        connector_type=None,
    )


# ---------------------------------------------------------------------------
# App discovery helpers
# ---------------------------------------------------------------------------

async def _fire_appdiscovery_cr(
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> ChangeRequest:
    """Create an agent_appdiscovery CR, generate its plan, then fire the workflow."""
    cr = ChangeRequest(
        organization_id=org_id,
        requester_id=user_id,
        title=f"Application discovery for asset {asset_id}",
        description="Automated application discovery triggered via API",
        change_type=ChangeType.agent_appdiscovery,
        target_asset_ids=[str(asset_id)],
        desired_outcome={"asset_id": str(asset_id)},
        risk_level=RiskLevel.low,
        status=ChangeRequestStatus.draft,
    )
    db.add(cr)
    await db.flush()

    # Generate plan
    asset_result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(Asset.id == asset_id)
    )
    asset = asset_result.scalar_one_or_none()
    assets = [asset] if asset else []

    safety_result = score_change_request(cr, assets)
    plan_data = generate_plan(cr, assets, safety_result)

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
    await db.commit()
    await db.refresh(cr)

    # Fire workflow in background
    wf_input = workflow_runner.WorkflowInput(
        change_request_id=str(cr.id),
        organization_id=str(org_id),
        initiator_id=str(user_id),
    )
    workflow_id = f"appdiscovery-{cr.id}"
    asyncio.create_task(
        workflow_runner.start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)
    )

    return cr


async def _poll_cr_until_done(cr_id: uuid.UUID, timeout: int = 300) -> ChangeRequest:
    """Poll a CR until it reaches a terminal status or timeout is exceeded."""
    terminal = {
        ChangeRequestStatus.completed,
        ChangeRequestStatus.failed,
        ChangeRequestStatus.rolled_back,
        ChangeRequestStatus.completed_with_errors,
    }
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChangeRequest).where(ChangeRequest.id == cr_id)
            )
            cr = result.scalar_one_or_none()
        if cr and cr.status in terminal:
            return cr
        if asyncio.get_event_loop().time() >= deadline:
            return cr
        await asyncio.sleep(3)


@router.post("/{asset_id}/discover-applications")
async def discover_applications(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger agent-based application discovery on an asset and wait for results."""
    # Verify asset belongs to user's org
    asset_result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.organization_id == user.organization_id,
        )
    )
    asset = asset_result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    cr = await _fire_appdiscovery_cr(asset_id, user.organization_id, user.id, db)
    cr = await _poll_cr_until_done(cr.id, timeout=300)

    # Re-read asset to get updated metadata written by executor
    async with AsyncSessionLocal() as session:
        refreshed_result = await session.execute(
            select(Asset).where(Asset.id == asset_id)
        )
        refreshed_asset = refreshed_result.scalar_one_or_none()

    metadata = (refreshed_asset.asset_metadata or {}) if refreshed_asset else {}
    applications = metadata.get("applications", [])

    return {
        "cr_id": str(cr.id) if cr else None,
        "status": cr.status.value if cr else "timeout",
        "applications": applications,
    }
