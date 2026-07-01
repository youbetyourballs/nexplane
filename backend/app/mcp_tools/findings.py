# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Findings domain (12 tools).

All tools authenticate via API token passed as the `token` argument.
Write operations that touch external systems produce CRs in draft state.
Direct status updates (assign, accept_risk, etc.) modify Nexplane metadata only.
"""
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


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
async def list_findings(
    token: str,
    status: str = None,
    severity: str = None,
    cve_id: str = None,
    asset_id: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List vulnerability findings for the authenticated org.
    Filter by status (open, actionable, remediating, resolved, etc.),
    severity (critical/high/medium/low), CVE ID, or asset ID.
    Returns summary fields — use get_finding for full detail.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(VulnerabilityFinding)
            .where(VulnerabilityFinding.organization_id == user.organization_id)
            .order_by(VulnerabilityFinding.ingested_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(VulnerabilityFinding.status == status)
        if severity:
            stmt = stmt.where(VulnerabilityFinding.severity == severity)
        if cve_id:
            stmt = stmt.where(VulnerabilityFinding.cve_id == cve_id)
        if asset_id:
            stmt = stmt.where(VulnerabilityFinding.asset_id == _uuid.UUID(asset_id))

        result = await db.execute(stmt)
        findings = result.scalars().all()
        return [
            {
                "id": str(f.id),
                "cve_id": f.cve_id,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "asset_id": str(f.asset_id) if f.asset_id else None,
                "scanner": f.scanner,
                "ingested_at": f.ingested_at.isoformat() if f.ingested_at else None,
            }
            for f in findings
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_finding(token: str, finding_id: str) -> dict[str, Any]:
    """
    Get full detail for a single finding including PoC result, verification status,
    linked CRs, and SLA countdown. Use this after list_findings to get depth on a specific item.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding, FindingChangeRequest
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}

        fcr_result = await db.execute(
            select(FindingChangeRequest, ChangeRequest)
            .join(ChangeRequest, ChangeRequest.id == FindingChangeRequest.cr_id)
            .where(FindingChangeRequest.finding_id == f.id)
            .order_by(FindingChangeRequest.created_at.desc())
        )
        linked_crs = [
            {
                "cr_id": str(fcr.cr_id),
                "role": fcr.role,
                "cr_status": str(cr.status),
                "cr_title": cr.title,
            }
            for fcr, cr in fcr_result.all()
        ]

        return {
            "id": str(f.id),
            "cve_id": f.cve_id,
            "title": f.title,
            "description": f.description,
            "severity": f.severity,
            "status": f.status,
            "scanner": f.scanner,
            "asset_id": str(f.asset_id) if f.asset_id else None,
            "affected_package": f.affected_package,
            "affected_version": f.affected_version,
            "fixed_version": f.fixed_version,
            "exploitability_result": f.exploitability_result,
            "poc_source": f.poc_source,
            "verification_result": f.verification_result,
            "verified_at": f.verified_at.isoformat() if f.verified_at else None,
            "verification_failed": f.verification_failed,
            "accepted_risk_reason": f.accepted_risk_reason,
            "accepted_risk_expires_at": f.accepted_risk_expires_at.isoformat() if f.accepted_risk_expires_at else None,
            "ingested_at": f.ingested_at.isoformat() if f.ingested_at else None,
            "linked_change_requests": linked_crs,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def update_finding_status(
    token: str,
    finding_id: str,
    status: str,
) -> dict[str, Any]:
    """
    Update a finding's lifecycle status. Updates Nexplane metadata only — does not
    touch external systems. Valid values: open, accepted_risk, false_positive,
    exploitability_pending, actionable, remediating, verifying, resolved.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        f.status = status
        await db.commit()
        return {"id": str(f.id), "status": f.status}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def assign_finding(token: str, finding_id: str, user_id: str) -> dict[str, Any]:
    """Assign a finding to a Nexplane user by their user ID. Updates Nexplane metadata only."""
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        f.assigned_to_user_id = _uuid.UUID(user_id)
        await db.commit()
        return {"id": str(f.id), "assigned_to_user_id": user_id}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def accept_risk(
    token: str,
    finding_id: str,
    reason: str,
    expires_at: str,
) -> dict[str, Any]:
    """
    Mark a finding as risk-accepted with a stated reason and expiry date (ISO 8601 format).
    The finding reappears on the SLA dashboard at expiry for re-review.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        f.status = "accepted_risk"
        f.accepted_risk_reason = reason
        f.accepted_risk_expires_at = datetime.fromisoformat(expires_at)
        await db.commit()
        return {"id": str(f.id), "status": f.status, "expires_at": expires_at}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def mark_false_positive(token: str, finding_id: str) -> dict[str, Any]:
    """
    Close a finding as a false positive. No SLA credit is given.
    Not reversible without re-ingesting the finding from the scanner.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        f.status = "false_positive"
        await db.commit()
        return {"id": str(f.id), "status": "false_positive"}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def trigger_poc_validation(
    token: str,
    finding_id: str,
    asset_id: str = None,
) -> dict[str, Any]:
    """
    Trigger a PoC validation run for this finding. If the CVE is on the CISA KEV catalog,
    the finding is immediately marked exploited. Otherwise creates a vuln_poc_validate CR
    in draft state. Returns the result and asset context bundle for planning.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.services.vuln_poc_service import check_cisa_kev
    from app.services.vuln_remediation_engine import link_cr_to_finding
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}

        # CISA KEV fast path — no CR needed
        if check_cisa_kev(f.cve_id or ""):
            f.status = "actionable"
            f.exploitability_result = "exploited"
            f.poc_source = "cisa_kev"
            await db.commit()
            ctx = await build_asset_context(f.asset_id, db) if f.asset_id else {}
            return {"exploitability_result": "exploited", "poc_source": "cisa_kev", "asset_context": ctx}

        target_id = asset_id or (str(f.asset_id) if f.asset_id else None)
        cr = ChangeRequest(
            organization_id=user.organization_id,
            requester_id=user.id,
            change_type="vuln_poc_validate",
            title=f"PoC validate: {f.cve_id or f.title}",
            status=ChangeRequestStatus.draft,
            target_asset_ids=[target_id] if target_id else [],
            desired_outcome={
                "cve_id": f.cve_id,
                "poc_source": "nessus_plugin",
                "poc_ref": "",
            },
        )
        db.add(cr)
        f.status = "exploitability_pending"
        await db.flush()
        await link_cr_to_finding(db, f.id, cr.id, "poc_validate")
        await db.commit()

        ctx = await build_asset_context(f.asset_id, db) if f.asset_id else {}
        return {"cr_id": str(cr.id), "status": f.status, "asset_context": ctx}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_poc_result(token: str, finding_id: str) -> dict[str, Any]:
    """
    Get the current PoC validation result for a finding.
    Result values: exploited, not_exploited, inconclusive, no_poc_available, or null if not yet run.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        return {
            "exploitability_result": f.exploitability_result,
            "poc_source": f.poc_source,
            "poc_ref": f.poc_ref,
            "status": f.status,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def challenge_exploitability(
    token: str,
    finding_id: str,
    reason: str,
) -> dict[str, Any]:
    """
    Submit an exploitability challenge explaining why this CVE is not exploitable in your
    environment. Disabled for CISA KEV findings — those cannot be challenged.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        if f.poc_source == "cisa_kev":
            return {"error": "CISA KEV findings cannot be challenged"}
        f.status = "challenged"
        f.exploitability_challenged_at = datetime.now(timezone.utc)
        f.exploitability_challenge_reason = reason
        await db.commit()
        return {"id": str(f.id), "status": f.status}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def trigger_verification(token: str, finding_id: str) -> dict[str, Any]:
    """
    Trigger a scanner re-probe to verify remediation actually removed the vulnerability.
    Returns the verification result and asset context bundle.
    Result values: resolved, still_vulnerable, inconclusive.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding
    from app.services.vuln_verification_service import trigger_verification as _trigger
    from app.mcp_tools.context import build_asset_context

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}

        probe = await _trigger(f, db)
        f.verification_result = probe["result"]
        f.verified_at = datetime.now(timezone.utc)
        if probe["result"] == "resolved":
            f.status = "resolved"
            f.verification_failed = False
        elif probe["result"] == "still_vulnerable":
            f.status = "open"
            f.verification_failed = True
        await db.commit()

        ctx = await build_asset_context(f.asset_id, db) if f.asset_id else {}
        return {**probe, "asset_context": ctx}
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_verification_result(token: str, finding_id: str) -> dict[str, Any]:
    """Get the latest scanner verification probe result for a finding."""
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        f = result.scalar_one_or_none()
        if f is None:
            return {"error": "Finding not found"}
        return {
            "verification_result": f.verification_result,
            "verified_at": f.verified_at.isoformat() if f.verified_at else None,
            "verification_failed": f.verification_failed,
            "status": f.status,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_finding_change_requests(token: str, finding_id: str) -> list[dict[str, Any]]:
    """
    List all Change Requests linked to a finding (patch, mitigation, poc_validate, verify)
    with their current status. Use this to track remediation progress from the finding.
    """
    from sqlalchemy import select
    from app.models.vulnerability import VulnerabilityFinding, FindingChangeRequest
    from app.models.change_request import ChangeRequest

    user, db, db_cm = await _auth(token)
    try:
        f_result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.id == _uuid.UUID(finding_id),
                VulnerabilityFinding.organization_id == user.organization_id,
            )
        )
        if f_result.scalar_one_or_none() is None:
            return [{"error": "Finding not found"}]

        fcr_result = await db.execute(
            select(FindingChangeRequest, ChangeRequest)
            .join(ChangeRequest, ChangeRequest.id == FindingChangeRequest.cr_id)
            .where(FindingChangeRequest.finding_id == _uuid.UUID(finding_id))
            .order_by(FindingChangeRequest.created_at.desc())
        )
        return [
            {
                "cr_id": str(fcr.cr_id),
                "role": fcr.role,
                "cr_status": str(cr.status),
                "cr_title": cr.title,
                "created_at": fcr.created_at.isoformat(),
            }
            for fcr, cr in fcr_result.all()
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
