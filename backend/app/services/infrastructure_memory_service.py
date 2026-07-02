# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Infrastructure Memory Service

Answers provenance questions about assets and infrastructure state:
  1. Why is port X open on asset Y?          → get_asset_provenance
  2. Who approved firewall rule for CIDR?    → search_crs_by_parameter
  3. Which apps depend on cert X?            → get_asset_dependents
  4. Can asset X be deleted safely?          → can_asset_be_deleted
  5. What changed in region/VPC yesterday?  → get_timeline_summary
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_dependency import AssetDependency
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.approval import Approval
from app.models.change_plan import ChangePlan
from app.models.user import User


# ---------------------------------------------------------------------------
# 1. Asset provenance — why does this asset / config key exist?
# ---------------------------------------------------------------------------

async def get_asset_provenance(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    config_key: Optional[str] = None,
) -> dict:
    """Return the CR history that explains why an asset or config value exists.

    Args:
        config_key: optional free-text filter (e.g. "8443", "10.2.0.0/16") that
                    is matched against CR title, description, and parameters JSON.
    """
    # Fetch asset
    asset_row = await db.execute(
        select(Asset).where(Asset.id == asset_id, Asset.organization_id == org_id)
    )
    asset = asset_row.scalar_one_or_none()
    if asset is None:
        return {"error": "asset_not_found", "asset_id": str(asset_id)}

    # Fetch completed CRs that touch this asset
    stmt = (
        select(ChangeRequest, User)
        .join(User, User.id == ChangeRequest.requester_id)
        .where(
            ChangeRequest.organization_id == org_id,
            ChangeRequest.status.in_([
                ChangeRequestStatus.completed,
                ChangeRequestStatus.rolled_back,
            ]),
            ChangeRequest.target_asset_ids.cast(String).contains(str(asset_id)),
        )
        .order_by(ChangeRequest.updated_at.desc().nullslast())
        .limit(50)
    )
    rows = await db.execute(stmt)
    pairs = rows.all()

    # Fetch approvals for these CRs in one query
    cr_ids = [p[0].id for p in pairs]
    approvals_by_cr: dict[uuid.UUID, list[dict]] = {}
    if cr_ids:
        ap_stmt = (
            select(Approval, User)
            .join(User, User.id == Approval.approver_id)
            .where(Approval.change_request_id.in_(cr_ids))
        )
        ap_rows = await db.execute(ap_stmt)
        for appr, appr_user in ap_rows.all():
            approvals_by_cr.setdefault(appr.change_request_id, []).append({
                "approver_id": str(appr.approver_id),
                "approver_name": appr_user.email,
                "decision": appr.decision.value,
                "comment": appr.comment,
                "approved_at": appr.created_at.isoformat() if appr.created_at else None,
            })

    # Fetch rollback availability from change_plans
    plan_stmt = select(ChangePlan).where(ChangePlan.change_request_id.in_(cr_ids))
    plan_rows = await db.execute(plan_stmt)
    plans_by_cr = {p.change_request_id: p for p in plan_rows.scalars().all()}

    entries = []
    for cr, requester in pairs:
        # Optional keyword filter over CR text and parameters
        if config_key:
            haystack = " ".join([
                cr.title or "",
                cr.description or "",
                str(cr.desired_outcome or ""),
            ]).lower()
            if config_key.lower() not in haystack:
                continue

        plan = plans_by_cr.get(cr.id)
        rollback_available = False
        rollback_strategy = None
        if plan:
            rp = (plan.plan_data or {}).get("rollback_plan", {})
            rollback_available = bool(rp.get("automatic", False) or rp.get("steps"))
            rollback_strategy = rp.get("strategy")

        entries.append({
            "cr_id": str(cr.id),
            "change_type": cr.change_type.value if hasattr(cr.change_type, "value") else str(cr.change_type),
            "title": cr.title,
            "description": cr.description,
            "requester": requester.email,
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "applied_at": cr.updated_at.isoformat() if cr.updated_at else None,
            "stateful_approved_at": cr.stateful_approved_at.isoformat() if cr.stateful_approved_at else None,
            "approvals": approvals_by_cr.get(cr.id, []),
            "rollback_available": rollback_available,
            "rollback_strategy": rollback_strategy,
            "artifact_refs": cr.artifact_refs,
            "notes": cr.description,
        })

    meta = asset.asset_metadata or {}
    return {
        "asset_id": str(asset_id),
        "asset_name": asset.name,
        "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
        "owner": meta.get("owner"),
        "why_exists": meta.get("why_exists"),
        "open_ports": meta.get("open_ports", []),
        "firewall_rules": meta.get("firewall_rules", []),
        "config_key_filter": config_key,
        "change_history": entries,
    }


# ---------------------------------------------------------------------------
# 2. Search CRs by parameter value (e.g. CIDR, port number, rule name)
# ---------------------------------------------------------------------------

async def search_crs_by_parameter(
    db: AsyncSession,
    org_id: uuid.UUID,
    parameter_value: str,
    change_type_filter: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Find CRs whose title, description, or desired_outcome contain the given value.

    Used for: "who approved the firewall rule for 10.2.0.0/16?"
    """
    pattern = f"%{parameter_value}%"
    stmt = (
        select(ChangeRequest, User)
        .join(User, User.id == ChangeRequest.requester_id)
        .where(
            ChangeRequest.organization_id == org_id,
            or_(
                ChangeRequest.title.ilike(pattern),
                ChangeRequest.description.ilike(pattern),
                cast(ChangeRequest.desired_outcome, String).ilike(pattern),
            ),
        )
        .order_by(ChangeRequest.created_at.desc())
        .limit(limit)
    )
    if change_type_filter:
        from app.models.change_request import ChangeType
        try:
            ct = ChangeType(change_type_filter)
            stmt = stmt.where(ChangeRequest.change_type == ct)
        except ValueError:
            pass

    rows = await db.execute(stmt)
    pairs = rows.all()

    cr_ids = [p[0].id for p in pairs]
    approvals_by_cr: dict[uuid.UUID, list[dict]] = {}
    if cr_ids:
        ap_stmt = (
            select(Approval, User)
            .join(User, User.id == Approval.approver_id)
            .where(Approval.change_request_id.in_(cr_ids))
        )
        for appr, appr_user in (await db.execute(ap_stmt)).all():
            approvals_by_cr.setdefault(appr.change_request_id, []).append({
                "approver_id": str(appr.approver_id),
                "approver_name": appr_user.email,
                "decision": appr.decision.value,
                "approved_at": appr.created_at.isoformat() if appr.created_at else None,
                "comment": appr.comment,
            })

    plan_stmt = select(ChangePlan).where(ChangePlan.change_request_id.in_(cr_ids))
    plans_by_cr = {p.change_request_id: p for p in (await db.execute(plan_stmt)).scalars().all()}

    results = []
    for cr, requester in pairs:
        plan = plans_by_cr.get(cr.id)
        rp = {}
        if plan:
            rp = (plan.plan_data or {}).get("rollback_plan", {})

        last_verified = None
        if cr.verification_checks:
            timestamps = [
                v.get("checked_at") for v in cr.verification_checks
                if isinstance(v, dict) and v.get("checked_at")
            ]
            if timestamps:
                last_verified = max(timestamps)

        results.append({
            "cr_id": str(cr.id),
            "change_type": cr.change_type.value if hasattr(cr.change_type, "value") else str(cr.change_type),
            "title": cr.title,
            "description": cr.description,
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "requester": requester.email,
            "applied_at": cr.updated_at.isoformat() if cr.updated_at else None,
            "approvals": approvals_by_cr.get(cr.id, []),
            "rollback_available": bool(rp.get("automatic") or rp.get("steps")),
            "last_verified": last_verified,
            "target_asset_ids": cr.target_asset_ids,
        })
    return results


# ---------------------------------------------------------------------------
# 3. Asset dependents (what depends on this asset?)
# ---------------------------------------------------------------------------

async def get_asset_dependents(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> dict:
    """Return all assets that depend on asset_id, plus asset metadata.

    Used for: "which applications depend on cert wildcard.acme.internal?"
    """
    asset_row = await db.execute(
        select(Asset).where(Asset.id == asset_id, Asset.organization_id == org_id)
    )
    asset = asset_row.scalar_one_or_none()
    if asset is None:
        return {"error": "asset_not_found", "asset_id": str(asset_id)}

    # Query asset_dependencies where this asset is the provider
    dep_stmt = (
        select(AssetDependency, Asset)
        .join(Asset, Asset.id == AssetDependency.dependent_asset_id)
        .where(
            AssetDependency.organization_id == org_id,
            AssetDependency.dependency_asset_id == asset_id,
        )
    )
    dep_rows = await db.execute(dep_stmt)
    dependents = []
    for dep, dep_asset in dep_rows.all():
        dep_meta = dep_asset.asset_metadata or {}
        dependents.append({
            "asset_id": str(dep_asset.id),
            "asset_name": dep_asset.name,
            "asset_type": dep_asset.asset_type.value if hasattr(dep_asset.asset_type, "value") else str(dep_asset.asset_type),
            "environment": dep_asset.environment.value if hasattr(dep_asset.environment, "value") else str(dep_asset.environment),
            "dependency_type": dep.dependency_type,
            "dep_metadata": dep.dep_metadata,
            "owner": dep_meta.get("owner"),
        })

    # Also check asset_relationships (the older graph table)
    from app.models.asset_relationship import AssetRelationship
    rel_stmt = (
        select(AssetRelationship, Asset)
        .join(Asset, Asset.id == AssetRelationship.source_asset_id)
        .where(
            AssetRelationship.organization_id == org_id,
            AssetRelationship.target_asset_id == asset_id,
        )
    )
    rel_rows = await db.execute(rel_stmt)
    for rel, rel_asset in rel_rows.all():
        # Avoid duplicates
        rel_ids = {d["asset_id"] for d in dependents}
        if str(rel_asset.id) not in rel_ids:
            rel_meta = rel_asset.asset_metadata or {}
            dependents.append({
                "asset_id": str(rel_asset.id),
                "asset_name": rel_asset.name,
                "asset_type": rel_asset.asset_type.value if hasattr(rel_asset.asset_type, "value") else str(rel_asset.asset_type),
                "environment": rel_asset.environment.value if hasattr(rel_asset.environment, "value") else str(rel_asset.environment),
                "dependency_type": rel.relationship_type,
                "dep_metadata": rel.rel_metadata,
                "owner": rel_meta.get("owner"),
                "source": "asset_relationships",
            })

    meta = asset.asset_metadata or {}
    return {
        "asset_id": str(asset_id),
        "asset_name": asset.name,
        "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
        "owner": meta.get("owner"),
        "cert_expiry": meta.get("cert_expiry"),
        "cert_subject": meta.get("cert_subject"),
        "dependent_count": len(dependents),
        "dependents": dependents,
        "recommendation": _cert_recommendation(meta),
    }


def _cert_recommendation(meta: dict) -> Optional[str]:
    """Generate a human-readable recommendation for certificate assets."""
    expiry_str = meta.get("cert_expiry")
    if not expiry_str:
        return None
    try:
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_left = (expiry - now).days
        if days_left < 0:
            return f"EXPIRED {abs(days_left)} days ago — renew immediately."
        elif days_left < 14:
            return f"Expires in {days_left} days — renew urgently."
        elif days_left < 30:
            return f"Expires in {days_left} days — schedule renewal."
        else:
            return f"Valid for {days_left} more days."
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 4. Deletion safety check
# ---------------------------------------------------------------------------

async def can_asset_be_deleted(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> dict:
    """Determine whether it is safe to delete an asset.

    Returns:
        safe: bool — True only when no blocking reasons exist
        dependent_count: int
        last_deployed_at: ISO timestamp of most recent applied CR
        has_rollback_snapshot: bool — artifact_refs has a snapshot key
        requires_approval: bool — asset criticality is high/critical
        blocking_reasons: list[str]
    """
    asset_row = await db.execute(
        select(Asset).where(Asset.id == asset_id, Asset.organization_id == org_id)
    )
    asset = asset_row.scalar_one_or_none()
    if asset is None:
        return {"error": "asset_not_found", "asset_id": str(asset_id)}

    # Check dependents
    dep_count_stmt = select(func.count()).where(
        AssetDependency.organization_id == org_id,
        AssetDependency.dependency_asset_id == asset_id,
    )
    dep_count = (await db.execute(dep_count_stmt)).scalar_one()

    # Also check asset_relationships
    from app.models.asset_relationship import AssetRelationship
    rel_count_stmt = select(func.count()).where(
        AssetRelationship.organization_id == org_id,
        AssetRelationship.target_asset_id == asset_id,
    )
    rel_count = (await db.execute(rel_count_stmt)).scalar_one()
    total_dependents = dep_count + rel_count

    # Last deployed CR
    last_cr_stmt = (
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == org_id,
            ChangeRequest.status == ChangeRequestStatus.completed,
            ChangeRequest.target_asset_ids.cast(String).contains(str(asset_id)),
        )
        .order_by(ChangeRequest.updated_at.desc().nullslast())
        .limit(1)
    )
    last_cr_row = await db.execute(last_cr_stmt)
    last_cr = last_cr_row.scalar_one_or_none()
    last_deployed_at = last_cr.updated_at.isoformat() if (last_cr and last_cr.updated_at) else None

    # Rollback snapshot — check artifact_refs across all completed CRs
    snap_stmt = (
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == org_id,
            ChangeRequest.target_asset_ids.cast(String).contains(str(asset_id)),
            ChangeRequest.artifact_refs.isnot(None),
        )
        .order_by(ChangeRequest.updated_at.desc().nullslast())
        .limit(5)
    )
    snap_rows = await db.execute(snap_stmt)
    has_rollback_snapshot = False
    for snap_cr in snap_rows.scalars().all():
        refs = snap_cr.artifact_refs or {}
        if any("snapshot" in k.lower() or "ami" in k.lower() for k in refs.keys()):
            has_rollback_snapshot = True
            break

    # Criticality-based approval requirement
    crit_val = asset.criticality.value if hasattr(asset.criticality, "value") else str(asset.criticality)
    requires_approval = crit_val in ("high", "critical")

    # Blocking reasons
    blocking_reasons = []
    if total_dependents > 0:
        blocking_reasons.append(
            f"{total_dependents} asset(s) depend on this — removing it may cause outages."
        )
    if requires_approval:
        blocking_reasons.append(
            f"Asset criticality is '{crit_val}' — deletion requires explicit approval."
        )

    return {
        "asset_id": str(asset_id),
        "asset_name": asset.name,
        "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
        "criticality": crit_val,
        "safe": len(blocking_reasons) == 0,
        "dependent_count": total_dependents,
        "last_deployed_at": last_deployed_at,
        "has_rollback_snapshot": has_rollback_snapshot,
        "requires_approval": requires_approval,
        "blocking_reasons": blocking_reasons,
    }


# ---------------------------------------------------------------------------
# 5. Timeline summary — what changed in region/VPC/tag in a time window?
# ---------------------------------------------------------------------------

async def get_timeline_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: Optional[uuid.UUID] = None,
    tag_filter: Optional[str] = None,
    region: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
) -> dict:
    """Summarise what changed in a given scope and time window.

    Returns:
        total, approved_count, auto_remediation_count, rollback_count, changes list
    """
    stmt = select(ChangeRequest).where(ChangeRequest.organization_id == org_id)

    # Scope filter
    if asset_id:
        stmt = stmt.where(ChangeRequest.target_asset_ids.contains([str(asset_id)]))

    # Time window
    if since:
        stmt = stmt.where(ChangeRequest.created_at >= since)
    if until:
        stmt = stmt.where(ChangeRequest.created_at <= until)

    # Region filter — matched against CR title/description (no dedicated field)
    if region:
        pattern = f"%{region}%"
        stmt = stmt.where(
            or_(
                ChangeRequest.title.ilike(pattern),
                ChangeRequest.description.ilike(pattern),
                cast(ChangeRequest.desired_outcome, String).ilike(pattern),
            )
        )

    # Tag filter over title/description
    if tag_filter:
        pattern = f"%{tag_filter}%"
        stmt = stmt.where(
            or_(
                ChangeRequest.title.ilike(pattern),
                ChangeRequest.description.ilike(pattern),
            )
        )

    stmt = stmt.order_by(ChangeRequest.created_at.desc()).limit(limit)
    rows = await db.execute(stmt)
    crs = rows.scalars().all()

    approved_count = 0
    auto_remediation_count = 0
    rollback_count = 0
    changes = []

    for cr in crs:
        status_val = cr.status.value if hasattr(cr.status, "value") else str(cr.status)
        is_approved = cr.stateful_approved_at is not None
        is_auto = cr.source in ("auto_remediation", "vulnerability_remediation", "policy_drift")
        is_rollback = status_val in ("rolled_back", "rollback_partial")

        if is_approved:
            approved_count += 1
        if is_auto:
            auto_remediation_count += 1
        if is_rollback:
            rollback_count += 1

        changes.append({
            "cr_id": str(cr.id),
            "change_type": cr.change_type.value if hasattr(cr.change_type, "value") else str(cr.change_type),
            "title": cr.title,
            "status": status_val,
            "source": cr.source,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
            "applied_at": cr.updated_at.isoformat() if cr.updated_at else None,
            "target_asset_ids": cr.target_asset_ids,
            "is_auto_remediation": is_auto,
            "is_rollback": is_rollback,
        })

    return {
        "total": len(crs),
        "approved_count": approved_count,
        "auto_remediation_count": auto_remediation_count,
        "rollback_count": rollback_count,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "region_filter": region,
        "tag_filter": tag_filter,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Natural-language intent parser
# ---------------------------------------------------------------------------

_INTENT_PORT = re.compile(r"port\s+(\d+)", re.IGNORECASE)
_INTENT_CIDR = re.compile(r"(\d+\.\d+\.\d+\.\d+(?:/\d+)?)", re.IGNORECASE)
_INTENT_CERT = re.compile(r"cert(?:ificate)?\s+([\w.*-]+)", re.IGNORECASE)
_INTENT_DELETE = re.compile(r"(?:can|should|safe).*delet", re.IGNORECASE)
_INTENT_CHANGED = re.compile(r"(?:what|show|list).*changed?", re.IGNORECASE)
_INTENT_APPROVED = re.compile(r"who.*approv", re.IGNORECASE)
_INTENT_WHY = re.compile(r"why\s+(?:is|does|did|was)", re.IGNORECASE)
_INTENT_DEPEND = re.compile(r"depend", re.IGNORECASE)


def parse_query_intent(query: str) -> dict:
    """Lightweight pattern-based intent extraction.

    Returns a dict with keys:
      intent: "provenance" | "approval_search" | "dependents" | "deletion_check" | "timeline"
      entities: dict of extracted entities
    """
    q = query.strip()

    if _INTENT_DELETE.search(q):
        return {"intent": "deletion_check", "entities": {"query": q}}

    if _INTENT_CHANGED.search(q):
        region_match = re.search(r"(us-east-\d|us-west-\d|eu-\w+-\d|ap-\w+-\d)", q, re.IGNORECASE)
        cidr_match = _INTENT_CIDR.search(q)
        return {
            "intent": "timeline",
            "entities": {
                "region": region_match.group(1) if region_match else None,
                "cidr": cidr_match.group(1) if cidr_match else None,
                "query": q,
            },
        }

    if _INTENT_APPROVED.search(q):
        cidr_match = _INTENT_CIDR.search(q)
        port_match = _INTENT_PORT.search(q)
        return {
            "intent": "approval_search",
            "entities": {
                "cidr": cidr_match.group(1) if cidr_match else None,
                "port": port_match.group(1) if port_match else None,
                "query": q,
            },
        }

    if _INTENT_DEPEND.search(q) or _INTENT_CERT.search(q):
        cert_match = _INTENT_CERT.search(q)
        return {
            "intent": "dependents",
            "entities": {
                "cert_name": cert_match.group(1) if cert_match else None,
                "query": q,
            },
        }

    # Default: provenance
    port_match = _INTENT_PORT.search(q)
    return {
        "intent": "provenance",
        "entities": {
            "port": port_match.group(1) if port_match else None,
            "query": q,
        },
    }
