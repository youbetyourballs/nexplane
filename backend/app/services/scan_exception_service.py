# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.scan_exception import ScanException
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus

logger = logging.getLogger(__name__)


async def _get_exception(exception_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> ScanException:
    r = await db.execute(
        select(ScanException).where(
            ScanException.id == exception_id,
            ScanException.organization_id == org_id,
        )
    )
    exc = r.scalar_one_or_none()
    if not exc:
        raise ValueError(f"ScanException {exception_id} not found")
    return exc


async def list_exceptions(db: AsyncSession, organization_id: uuid.UUID, scan_cr_id: uuid.UUID = None, status: str = None) -> list:
    stmt = select(ScanException).where(ScanException.organization_id == organization_id)
    if scan_cr_id is not None:
        stmt = stmt.where(ScanException.scan_cr_id == scan_cr_id)
    if status is not None:
        stmt = stmt.where(ScanException.status == status)
    r = await db.execute(stmt)
    return r.scalars().all()


async def get_exception(db: AsyncSession, organization_id: uuid.UUID, exception_id: uuid.UUID):
    r = await db.execute(
        select(ScanException).where(
            ScanException.id == exception_id,
            ScanException.organization_id == organization_id,
        )
    )
    return r.scalar_one_or_none()


async def dismiss_exception(exception_id: uuid.UUID, reason: str, db: AsyncSession, org_id: uuid.UUID) -> dict:
    if not reason or not reason.strip():
        raise ValueError("reason is required when dismissing a scan exception")
    exc = await _get_exception(exception_id, org_id, db)
    exc.status = "dismissed"
    exc.resolution_notes = reason.strip()
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "dismissed", "exception_id": str(exception_id)}


async def resolve_with_update_cr(exception_id: uuid.UUID, update_cr_params: dict, db: AsyncSession, org_id: uuid.UUID) -> dict:
    exc = await _get_exception(exception_id, org_id, db)
    cr = ChangeRequest(
        id=uuid.uuid4(),
        organization_id=org_id,
        change_type=ChangeType.update_reference,
        asset_id=exc.consumer_asset_id,
        title=f"Update reference at {exc.location}",
        parameters=update_cr_params,
        status=ChangeRequestStatus.draft,
        created_at=datetime.now(timezone.utc),
    )
    db.add(cr)
    exc.status = "resolved"
    exc.resolved_by_cr_id = cr.id
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "resolved", "exception_id": str(exception_id), "cr_id": str(cr.id)}


async def resolve_exception(db: AsyncSession, organization_id: uuid.UUID, exception_id: uuid.UUID, resolution: dict) -> dict:
    path = resolution.get("path")
    if path == "resolve-with-cr":
        cr_id = resolution.get("cr_id")
        if not cr_id:
            raise ValueError("cr_id is required for resolve-with-cr path")
        exc = await _get_exception(exception_id, organization_id, db)
        exc.status = "resolved"
        exc.resolved_by_cr_id = uuid.UUID(str(cr_id)) if not isinstance(cr_id, uuid.UUID) else cr_id
        exc.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "resolved", "exception_id": str(exception_id), "cr_id": str(cr_id)}
    elif path == "reattempt-triage":
        exc = await _get_exception(exception_id, organization_id, db)
        from app.services.reference_triage import triage_scan_hits
        hit = {
            "hit": {
                "surface": exc.surface,
                "location": exc.location,
                "matched_term": exc.matched_term,
                "snippet": exc.snippet,
            },
            "asset_id": exc.consumer_asset_id,
            "tier": 2,
            "confidence": 0.5,
        }
        settings = resolution.get("settings")
        secrets_svc = resolution.get("secrets_svc")
        operator_context = resolution.get("operator_context", "")
        triage = await triage_scan_hits([hit], {"operator_context": operator_context, "source_term": exc.matched_term}, settings, secrets_svc)
        if triage.confident_updates:
            exc.status = "resolved"
            exc.resolution_notes = f"AI re-triage succeeded: {operator_context}"
            exc.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "resolved_by_retriage", "confident_updates": triage.confident_updates}
        if triage.exceptions:
            exc.reason = triage.exceptions[0].get("reason", exc.reason)
        exc.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "still_exception", "updated_reason": exc.reason}
    elif path == "dismiss-with-reason":
        reason = resolution.get("reason", "")
        return await dismiss_exception(exception_id, reason, db, organization_id)
    else:
        raise ValueError(f"Unknown resolution path: {path!r}. Must be one of: resolve-with-cr, reattempt-triage, dismiss-with-reason")


async def reattempt_triage(exception_id: uuid.UUID, operator_context: str, db: AsyncSession, org_id: uuid.UUID, settings, secrets_svc) -> dict:
    exc = await _get_exception(exception_id, org_id, db)
    from app.services.reference_triage import triage_scan_hits
    hit = {
        "hit": {
            "surface": exc.surface,
            "location": exc.location,
            "matched_term": exc.matched_term,
            "snippet": exc.snippet,
        },
        "asset_id": exc.consumer_asset_id,
        "tier": 2,
        "confidence": 0.5,
    }
    triage = await triage_scan_hits([hit], {"operator_context": operator_context, "source_term": exc.matched_term}, settings, secrets_svc)
    if triage.confident_updates:
        exc.status = "resolved"
        exc.resolution_notes = f"AI re-triage succeeded: {operator_context}"
        exc.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "resolved_by_retriage", "confident_updates": triage.confident_updates}
    exc.reason = triage.exceptions[0]["reason"] if triage.exceptions else exc.reason
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "still_exception", "updated_reason": exc.reason}


async def create_findings_for_unresolved(scan_cr_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> int:
    from app.models.vulnerability import VulnerabilityFinding
    r = await db.execute(
        select(ScanException).where(
            ScanException.scan_cr_id == scan_cr_id,
            ScanException.organization_id == org_id,
            ScanException.status == "pending",
        )
    )
    unresolved = r.scalars().all()
    for exc in unresolved:
        db.add(VulnerabilityFinding(
            id=uuid.uuid4(),
            organization_id=org_id,
            asset_id=exc.consumer_asset_id,
            finding_type="reference_not_updated",
            status="open",
            raw_payload={
                "scan_exception_id": str(exc.id),
                "location": exc.location,
                "surface": exc.surface,
                "matched_term": exc.matched_term,
                "snippet": exc.snippet,
                "reason": exc.reason,
            },
        ))
    if unresolved:
        await db.commit()
    return len(unresolved)
