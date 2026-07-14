# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Reference Scan domain (6 tools).

Covers the scan_for_references CR type and exception resolution workflow:
scan_for_references → get_scan_results → list_reference_exceptions →
resolve_reference_exception / reattempt_reference_triage / dismiss_reference_exception
"""
import logging
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def scan_for_references(
    token: str,
    search_terms: list[str],
    connector_ids: list[str],
    migration_context: str,
    asset_id: Optional[str] = None,
) -> dict:
    """
    Create a scan_for_references CR in draft state.

    Searches for references to resources being migrated across connectors.
    Returns the CR id and initial status. Use get_scan_results to poll until completed.

    Args:
        search_terms: List of strings/hostnames/IPs/ARNs to search for.
        connector_ids: List of connector IDs to search across.
        migration_context: Human-readable description of why the scan is happening.
        asset_id: Optional source asset being migrated (used for context).
    """
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
    from datetime import datetime, timezone

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        cr_id = _uuid.uuid4()
        parameters = {
            "search_terms": search_terms,
            "connector_ids": connector_ids,
            "migration_context": migration_context,
        }
        if asset_id:
            parameters["asset_id"] = asset_id
        target_asset_ids = [asset_id] if asset_id else []
        cr = ChangeRequest(
            id=cr_id,
            organization_id=org_id,
            change_type=ChangeType.scan_for_references,
            target_asset_ids=target_asset_ids,
            title=f"Scan for references: {migration_context[:80]}",
            desired_outcome=parameters,
            status=ChangeRequestStatus.draft,
            created_at=datetime.now(timezone.utc),
        )
        db.add(cr)
        await db.commit()
        return {
            "scan_cr_id": str(cr_id),
            "status": "draft",
            "connector_count": len(connector_ids),
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_scan_results(token: str, scan_cr_id: str) -> dict:
    """
    Get the current status and result summary for a scan_for_references CR.

    Returns status, total hit count, confident update count, and exception counts.
    Poll until status=completed before reviewing results.

    Args:
        scan_cr_id: The CR id returned by scan_for_references.
    """
    from app.models.change_request import ChangeRequest
    from app.models.scan_exception import ScanException
    from sqlalchemy import select, func

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        cr_result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(scan_cr_id),
                ChangeRequest.organization_id == org_id,
            )
        )
        cr = cr_result.scalar_one_or_none()
        if cr is None:
            return {"error": f"Scan CR {scan_cr_id} not found"}

        exc_result = await db.execute(
            select(ScanException).where(
                ScanException.scan_cr_id == _uuid.UUID(scan_cr_id),
                ScanException.organization_id == org_id,
            )
        )
        exceptions = exc_result.scalars().all()
        exceptions_pending = sum(1 for e in exceptions if e.status == "pending")
        execution_result = cr.execution_result or {}
        hits_total = execution_result.get("hits_total", 0)
        confident_updates = execution_result.get("confident_updates", 0)
        return {
            "scan_cr_id": scan_cr_id,
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "hits_total": hits_total,
            "confident_updates": confident_updates,
            "exceptions_total": len(exceptions),
            "exceptions_pending": exceptions_pending,
        }
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_reference_exceptions(token: str, scan_cr_id: str) -> list[dict]:
    """
    List all scan exceptions for a completed scan_for_references CR.

    Each exception is an ambiguous hit that requires operator review.
    Use resolve_reference_exception, reattempt_reference_triage, or
    dismiss_reference_exception to handle each one.

    Args:
        scan_cr_id: The CR id returned by scan_for_references.
    """
    from app.services.scan_exception_service import list_exceptions

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        exceptions = await list_exceptions(db, org_id, scan_cr_id=_uuid.UUID(scan_cr_id))
        return [
            {
                "id": str(e.id),
                "surface": e.surface,
                "location": e.location,
                "matched_term": e.matched_term,
                "snippet": e.snippet,
                "reason": e.reason,
                "suggested_action": e.suggested_action,
                "confidence": e.confidence,
                "status": e.status,
                "consumer_asset_id": str(e.consumer_asset_id) if e.consumer_asset_id else None,
            }
            for e in exceptions
        ]
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def resolve_reference_exception(
    token: str,
    exception_id: str,
    update_cr_params: dict,
) -> dict:
    """
    Resolve a scan exception by creating an update_reference CR with explicit params.

    Use this when you can determine the correct update (old_value, new_value, location).
    Creates a draft update_reference CR linked to the exception.

    Args:
        exception_id: The exception id from list_reference_exceptions.
        update_cr_params: Dict with update parameters (old_value, new_value, etc.).
    """
    from app.services.scan_exception_service import resolve_with_update_cr

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        return await resolve_with_update_cr(
            _uuid.UUID(exception_id), update_cr_params, db, org_id
        )
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def reattempt_reference_triage(
    token: str,
    exception_id: str,
    operator_context: str = "",
) -> dict:
    """
    Re-run AI triage on a scan exception with additional operator context.

    Use this when you have more information that might help the AI classify the hit.
    If the AI becomes confident, the exception is marked resolved automatically.
    If still ambiguous, the reason is updated with refined analysis.

    Args:
        exception_id: The exception id from list_reference_exceptions.
        operator_context: Additional context to help AI make a decision.
    """
    from app.services.scan_exception_service import reattempt_triage
    from app.models.org_settings import OrganizationSettings
    from app.services.secrets_service import SecretsService
    from app.config import settings as app_settings
    from sqlalchemy import select

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        settings_result = await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id == org_id
            )
        )
        org_settings = settings_result.scalar_one_or_none()
        if not org_settings or not org_settings.anthropic_api_key_encrypted:
            return {"error": "AI service not configured for this organization"}
        secrets_svc = SecretsService(app_settings.SECRET_KEY)
        return await reattempt_triage(
            _uuid.UUID(exception_id), operator_context, db, org_id,
            org_settings, secrets_svc,
        )
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def dismiss_reference_exception(
    token: str,
    exception_id: str,
    reason: str,
) -> dict:
    """
    Dismiss a scan exception with a documented reason.

    Use when the hit is a false positive or the reference intentionally stays unchanged.
    Reason is required and will appear in audit trail.
    Unresolved exceptions at execution time become reference_not_updated findings.

    Args:
        exception_id: The exception id from list_reference_exceptions.
        reason: Required explanation for why this exception is being dismissed.
    """
    from app.services.scan_exception_service import dismiss_exception

    user, db, db_cm = await _auth(token)
    try:
        org_id = user.organization_id
        return await dismiss_exception(_uuid.UUID(exception_id), reason, db, org_id)
    except ValueError as e:
        return {"error": str(e)}
    finally:
        await db_cm.__aexit__(None, None, None)
