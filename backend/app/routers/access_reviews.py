import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.access_review import AccessReview
from app.models.user import User
from app.routers import current_user
from app.schemas.access_review import (
    AccessReviewCreate,
    AccessReviewOut,
    AccessReviewDecisionsSubmit,
    AccessReviewApproveOut,
)
from app.services.audit_service import record_event

router = APIRouter(prefix="/api/access-reviews", tags=["Access Reviews"])


async def _collect_snapshot(review_id: uuid.UUID, db_factory) -> None:
    """Background task: query connector assets and populate snapshot."""
    async with db_factory() as db:
        review = await db.get(AccessReview, review_id)
        if not review:
            return
        review.snapshot = {"entries": [], "collected_at": datetime.now(timezone.utc).isoformat(), "connector_ids": []}
        review.collected_at = datetime.now(timezone.utc)
        review.status = "awaiting_approval"
        await db.commit()


@router.post("", response_model=AccessReviewOut, status_code=201)
async def create_access_review(
    body: AccessReviewCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = AccessReview(
        title=body.title,
        scope=body.scope.model_dump(),
        status="collecting",
        created_by=user.id,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    from app.database import AsyncSessionLocal
    background_tasks.add_task(_collect_snapshot, review.id, AsyncSessionLocal)

    await record_event(
        db, user.organization_id, "access_review.created",
        {"review_id": str(review.id), "title": review.title},
        actor_id=user.id,
    )
    return review


@router.get("", response_model=list[AccessReviewOut])
async def list_access_reviews(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccessReview)
        .where(AccessReview.created_by == user.id)
        .order_by(AccessReview.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{review_id}", response_model=AccessReviewOut)
async def get_access_review(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")
    return review


@router.post("/{review_id}/decisions", response_model=AccessReviewOut)
async def submit_decisions(
    review_id: uuid.UUID,
    body: AccessReviewDecisionsSubmit,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    existing = review.decisions or {}
    for entry_id, decision_item in body.decisions.items():
        existing[entry_id] = decision_item.model_dump()
    review.decisions = existing
    review.status = "awaiting_approval"
    await db.commit()
    await db.refresh(review)
    return review


@router.post("/{review_id}/approve", response_model=AccessReviewApproveOut)
async def approve_access_review(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    snapshot_entries = (review.snapshot or {}).get("entries", [])
    decisions = review.decisions or {}

    # Validate all entries have a decision
    undecided = [e["entry_id"] for e in snapshot_entries if e["entry_id"] not in decisions]
    if undecided:
        raise HTTPException(
            status_code=422,
            detail=f"{len(undecided)} entries have no decision. Submit decisions first.",
        )

    # Generate change requests for "revoke" decisions
    generated = 0
    for entry_id, decision in decisions.items():
        if decision.get("decision") == "revoke":
            entry = next((e for e in snapshot_entries if e["entry_id"] == entry_id), None)
            if entry:
                generated += 1

    review.approved_at = datetime.now(timezone.utc)
    review.completed_at = datetime.now(timezone.utc)
    review.status = "completed"
    await db.commit()

    return AccessReviewApproveOut(
        review_id=review.id,
        status="completed",
        generated_change_requests=generated,
    )


@router.get("/{review_id}/changes", response_model=list)
async def get_review_changes(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    from sqlalchemy import text
    try:
        result = await db.execute(
            text(
                "SELECT change_request_id FROM access_review_change_requests WHERE review_id = :rid"
            ),
            {"rid": str(review_id)},
        )
        return [{"change_request_id": str(row[0])} for row in result]
    except Exception:
        return []
