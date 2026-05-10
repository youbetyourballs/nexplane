import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
from app.models.forensic_bundle import ForensicBundle
from app.models.ir_playbook_template import IRPlaybookTemplate
from app.models.user import User, UserRole
from app.routers import current_user

router = APIRouter(prefix="/api/ir", tags=["Incident Response"])


def _require_ir_role(user: User) -> None:
    if user.role not in (UserRole.ir_responder, UserRole.admin):
        raise HTTPException(status_code=403, detail="Incident Response actions require ir_responder or admin role")


@router.get("/templates")
async def list_ir_templates(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all IR playbook templates."""
    result = await db.execute(select(IRPlaybookTemplate).order_by(IRPlaybookTemplate.playbook_type))
    return result.scalars().all()


@router.post("/instantiate", status_code=201)
async def instantiate_ir_playbook(
    body: dict,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a change request from an IR playbook template.
    Merges template default_parameters with caller-supplied parameters.
    Requires ir_responder or admin role.
    """
    _require_ir_role(user)

    playbook_type = body.get("playbook_type")
    caller_params = body.get("parameters", {})

    # Look up template
    result = await db.execute(
        select(IRPlaybookTemplate).where(IRPlaybookTemplate.playbook_type == playbook_type)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail=f"IR playbook template '{playbook_type}' not found")

    # Merge parameters: template defaults < caller params
    merged_params = {**template.default_parameters, **caller_params}

    # Resolve ChangeType enum value
    try:
        change_type = ChangeType(playbook_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"No ChangeType mapping for playbook '{playbook_type}'")

    # Determine initial status
    initial_status = ChangeRequestStatus.draft
    if template.ir_auto_approve and user.role in (UserRole.ir_responder, UserRole.admin):
        initial_status = ChangeRequestStatus.approved

    cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"[IR] {template.display_name}",
        description=merged_params.get("reason", ""),
        change_type=change_type,
        target_asset_ids=[merged_params["asset_id"]] if "asset_id" in merged_params else [],
        desired_outcome={**merged_params, "ir_playbook_type": playbook_type, "ir_template_id": str(template.id)},
        status=initial_status,
    )
    db.add(cr)
    await db.flush()
    await db.refresh(cr)
    return cr


@router.get("/bundles")
async def list_ir_bundles(
    asset_id: str | None = Query(None),
    since: str | None = Query(None, description="RFC3339 timestamp"),
    until: str | None = Query(None, description="RFC3339 timestamp"),
    limit: int = Query(50, le=500),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List forensic bundles, filterable by asset and date range."""
    q = select(ForensicBundle).order_by(ForensicBundle.collected_at.desc()).limit(limit)
    if asset_id:
        q = q.where(ForensicBundle.asset_id == uuid.UUID(asset_id))
    if since:
        q = q.where(ForensicBundle.collected_at >= datetime.fromisoformat(since))
    if until:
        q = q.where(ForensicBundle.collected_at <= datetime.fromisoformat(until))

    result = await db.execute(q)
    return result.scalars().all()


@router.get("/bundles/{bundle_id}")
async def get_ir_bundle(
    bundle_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single forensic bundle manifest."""
    result = await db.execute(select(ForensicBundle).where(ForensicBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Forensic bundle not found")
    return bundle


@router.get("/bundles/{bundle_id}/download-url")
async def get_bundle_download_url(
    bundle_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a fresh pre-signed download URL for the bundle archive."""
    _require_ir_role(user)

    result = await db.execute(select(ForensicBundle).where(ForensicBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Forensic bundle not found")

    # For now return the stored upload URL as the download URL.
    # In production this should generate a fresh pre-signed GET URL via ir_forensics.
    return {"url": bundle.upload_url}
