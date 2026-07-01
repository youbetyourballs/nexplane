# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models.review_campaign import ReviewCampaign, ReviewEntry
from app.models.user import User
from app.routers import current_user
from app.schemas.review_campaign import (
    CampaignCreate, CampaignOut, ReviewEntryOut,
    EntryDecisionSubmit, CampaignApproveOut, EvidenceExport,
)
from app.services.review_collector import run_collection
from app.services.review_approver import generate_revocation_crs

router = APIRouter(prefix="/review-campaigns", tags=["Review Campaigns"])


def _assert_campaign(campaign, org_id: uuid.UUID) -> ReviewCampaign:
    if not campaign or campaign.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = ReviewCampaign(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        created_by=user.id,
        title=body.title,
        description=body.description,
        campaign_type=body.campaign_type,
        scope=body.scope.model_dump(),
        reviewer_assignment_rule=body.reviewer_assignment_rule.model_dump(),
        evidence_options=body.evidence_options.model_dump(),
        due_date=body.due_date,
        status="draft",
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    mine: bool = Query(False),
    status: str | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if mine:
        subq = select(ReviewEntry.campaign_id).where(ReviewEntry.reviewer_id == user.id).distinct()
        stmt = select(ReviewCampaign).where(
            ReviewCampaign.organization_id == user.organization_id,
            ReviewCampaign.id.in_(subq),
        )
    else:
        stmt = select(ReviewCampaign).where(ReviewCampaign.organization_id == user.organization_id)
    if status:
        stmt = stmt.where(ReviewCampaign.status == status)
    result = await db.execute(stmt.order_by(ReviewCampaign.created_at.desc()))
    return result.scalars().all()


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    return _assert_campaign(campaign, user.organization_id)


@router.post("/{campaign_id}/launch", response_model=CampaignOut)
async def launch_campaign(
    campaign_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    if campaign.status != "draft":
        raise HTTPException(status_code=422, detail=f"Cannot launch campaign in status '{campaign.status}'")
    campaign.status = "collecting"
    await db.commit()
    await db.refresh(campaign)
    background_tasks.add_task(run_collection, campaign_id, AsyncSessionLocal)
    return campaign


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    if campaign.status == "completed":
        raise HTTPException(status_code=422, detail="Cannot cancel a completed campaign")
    campaign.status = "cancelled"
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.get("/{campaign_id}/entries", response_model=list[ReviewEntryOut])
async def list_entries(
    campaign_id: uuid.UUID,
    reviewer_id: uuid.UUID | None = Query(None),
    decision: str | None = Query(None),
    flagged: bool | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    stmt = select(ReviewEntry).where(ReviewEntry.campaign_id == campaign_id)
    if reviewer_id:
        stmt = stmt.where(ReviewEntry.reviewer_id == reviewer_id)
    if decision == "pending":
        stmt = stmt.where(ReviewEntry.decision.is_(None))
    elif decision in ("keep", "revoke"):
        stmt = stmt.where(ReviewEntry.decision == decision)
    if flagged is True:
        stmt = stmt.where(ReviewEntry.is_privileged == True)  # noqa: E712
    result = await db.execute(stmt.order_by(ReviewEntry.user_email))
    return result.scalars().all()


@router.put("/{campaign_id}/entries/{entry_id}", response_model=ReviewEntryOut)
async def submit_decision(
    campaign_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: EntryDecisionSubmit,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    if campaign.status not in ("in_review", "awaiting_approval"):
        raise HTTPException(status_code=422, detail="Campaign is not accepting decisions")
    entry = await db.get(ReviewEntry, entry_id)
    if not entry or entry.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.decision = body.decision
    entry.decision_note = body.note
    entry.decided_at = datetime.now(timezone.utc)
    entry.decided_by = user.id
    pending_result = await db.execute(
        select(func.count()).where(
            ReviewEntry.campaign_id == campaign_id,
            ReviewEntry.decision.is_(None),
        )
    )
    pending = pending_result.scalar_one()
    if pending == 0 and campaign.status == "in_review":
        campaign.status = "awaiting_approval"
    await db.commit()
    await db.refresh(entry)
    return entry


@router.post("/{campaign_id}/approve", response_model=CampaignApproveOut)
async def approve_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    if campaign.status != "awaiting_approval":
        raise HTTPException(status_code=422, detail=f"Campaign must be awaiting_approval, got '{campaign.status}'")
    pending_result = await db.execute(
        select(func.count()).where(
            ReviewEntry.campaign_id == campaign_id,
            ReviewEntry.decision.is_(None),
        )
    )
    if pending_result.scalar_one() > 0:
        raise HTTPException(status_code=422, detail="Some entries have no decision")
    count = await generate_revocation_crs(campaign, user.id, db)
    campaign.status = "completed"
    campaign.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return CampaignApproveOut(campaign_id=campaign.id, status="completed", revocations_created=count)


@router.post("/{campaign_id}/entries/{entry_id}/create-remediation", status_code=201)
async def create_remediation_cr(
    campaign_id: uuid.UUID,
    entry_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a remediation change request for a revoke-decision access review entry."""
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    entry = await db.get(ReviewEntry, entry_id)
    if not entry or entry.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.decision != "revoke":
        raise HTTPException(status_code=400, detail="Entry must have decision=revoke to create remediation CR")
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
    cr = ChangeRequest(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"Offboard {entry.user_email} (access review {str(campaign_id)[:8]})",
        change_type=ChangeType.disable_iam_user,
        target_asset_ids=[],
        desired_outcome={"user_email": entry.user_email, "resource_name": entry.resource_name},
        status=ChangeRequestStatus.draft,
        source="access_review",
    )
    db.add(cr)
    entry.remediation_cr_id = cr.id
    await db.commit()
    return {"cr_id": str(cr.id), "entry_id": str(entry.id)}


@router.get("/{campaign_id}/evidence-export", response_model=EvidenceExport)
async def export_evidence(
    campaign_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(ReviewCampaign, campaign_id)
    _assert_campaign(campaign, user.organization_id)
    result = await db.execute(select(ReviewEntry).where(ReviewEntry.campaign_id == campaign_id))
    entries = result.scalars().all()
    return EvidenceExport(
        campaign=CampaignOut.model_validate(campaign),
        entries=[ReviewEntryOut.model_validate(e) for e in entries],
        exported_at=datetime.now(timezone.utc),
    )
