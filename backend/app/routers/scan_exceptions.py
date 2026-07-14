# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.dependencies import get_current_user
from app.services.scan_exception_service import (
    list_exceptions,
    get_exception,
    resolve_exception,
    dismiss_exception,
    resolve_with_update_cr,
    reattempt_triage,
)

router = APIRouter(prefix="/scan-exceptions", tags=["scan-exceptions"])


def _serialize(e) -> dict:
    return {
        "id": str(e.id),
        "matched_term": e.matched_term,
        "location": e.location,
        "surface": e.surface,
        "snippet": e.snippet,
        "reason": e.reason,
        "suggested_action": e.suggested_action,
        "confidence": e.confidence,
        "status": e.status,
        "resolution_notes": e.resolution_notes,
        "resolved_by_cr_id": str(e.resolved_by_cr_id) if e.resolved_by_cr_id else None,
        "consumer_asset_id": str(e.consumer_asset_id) if e.consumer_asset_id else None,
        "scan_cr_id": str(e.scan_cr_id),
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


@router.get("")
async def list_scan_exceptions(
    scan_cr_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    exceptions = await list_exceptions(db, current_user.organization_id, scan_cr_id=scan_cr_id, status=status)
    return [_serialize(e) for e in exceptions]


@router.get("/{exception_id}")
async def get_scan_exception(
    exception_id: uuid.UUID,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    exc = await get_exception(db, current_user.organization_id, exception_id)
    if not exc:
        raise HTTPException(status_code=404, detail=f"ScanException {exception_id} not found")
    return _serialize(exc)


class ResolveRequest(BaseModel):
    path: str
    cr_id: Optional[str] = None
    reason: Optional[str] = None
    operator_context: Optional[str] = None
    update_cr_params: Optional[dict] = None


@router.post("/{exception_id}/resolve")
async def resolve_scan_exception(
    exception_id: uuid.UUID,
    body: ResolveRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    resolution = body.model_dump(exclude_none=True)
    try:
        return await resolve_exception(db, current_user.organization_id, exception_id, resolution)
    except ValueError as e:
        msg = str(e)
        status_code = 400 if "required" in msg or "Unknown" in msg or "reason" in msg else 404
        raise HTTPException(status_code=status_code, detail=msg)
