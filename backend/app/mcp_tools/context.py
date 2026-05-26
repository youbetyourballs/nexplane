"""Asset context bundle — assembled automatically for planning-adjacent MCP tools."""
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def build_asset_context(asset_id: uuid.UUID, db: AsyncSession) -> dict[str, Any]:
    """
    Return a structured context bundle for an asset suitable for AI planning.
    All sub-queries are best-effort — missing data returns empty lists, not errors.
    """
    from app.models.asset import Asset
    from app.models.vulnerability import VulnerabilityFinding
    from app.models.change_request import ChangeRequest
    from app.models.connector import Connector

    # Asset record
    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        return {"error": f"Asset {asset_id} not found"}

    asset_dict = {
        "id": str(asset.id),
        "name": getattr(asset, "name", None),
        "ip_address": getattr(asset, "ip_address", None),
        "hostname": getattr(asset, "hostname", None),
        "os_type": getattr(asset, "os_type", None),
        "asset_type": str(getattr(asset, "asset_type", "")),
        "environment": str(getattr(asset, "environment", "")),
        "criticality": str(getattr(asset, "criticality", "")),
        "connector_id": str(asset.connector_id) if asset.connector_id else None,
    }

    # Open findings
    findings_result = await db.execute(
        select(VulnerabilityFinding)
        .where(
            VulnerabilityFinding.asset_id == asset_id,
            VulnerabilityFinding.status.notin_(["resolved", "false_positive", "accepted_risk"]),
        )
        .order_by(VulnerabilityFinding.ingested_at.desc())
        .limit(20)
    )
    findings = findings_result.scalars().all()
    findings_list = [
        {
            "id": str(f.id),
            "cve_id": f.cve_id,
            "title": f.title,
            "severity": f.severity,
            "status": f.status,
        }
        for f in findings
    ]

    # Recent CRs — filter by asset id present in target_asset_ids JSON array
    asset_id_str = str(asset_id)
    cr_result = await db.execute(
        select(ChangeRequest)
        .where(ChangeRequest.organization_id == asset.organization_id if hasattr(asset, 'organization_id') else True)
        .order_by(ChangeRequest.created_at.desc())
        .limit(10)
    )
    all_crs = cr_result.scalars().all()
    # Filter client-side for CRs referencing this asset
    crs = [cr for cr in all_crs if asset_id_str in (cr.target_asset_ids or [])]
    cr_list = [
        {
            "id": str(cr.id),
            "change_type": str(cr.change_type),
            "status": str(cr.status),
            "title": cr.title,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
        for cr in crs
    ]

    # Connector
    connector_list = []
    if asset.connector_id:
        conn_result = await db.execute(select(Connector).where(Connector.id == asset.connector_id))
        conn = conn_result.scalar_one_or_none()
        if conn:
            connector_list = [{"id": str(conn.id), "connector_type": conn.connector_type, "name": conn.name}]

    # Installed software (from asset raw_data if populated by connector discovery)
    installed_software = []
    raw = getattr(asset, "raw_data", None) or {}
    if isinstance(raw, dict):
        installed_software = raw.get("installed_packages", []) or raw.get("software", [])

    # Recent timeline events
    timeline_list = []
    try:
        from app.models.asset_timeline import AssetTimelineEvent
        tl_result = await db.execute(
            select(AssetTimelineEvent)
            .where(AssetTimelineEvent.asset_id == asset_id)
            .order_by(AssetTimelineEvent.occurred_at.desc())
            .limit(20)
        )
        timeline_events = tl_result.scalars().all()
        timeline_list = [
            {
                "event_type": e.event_type,
                "summary": getattr(e, "summary", ""),
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            }
            for e in timeline_events
        ]
    except Exception:
        pass

    return {
        "asset": asset_dict,
        "open_findings": findings_list,
        "recent_change_requests": cr_list,
        "recent_timeline_events": timeline_list,
        "connected_connectors": connector_list,
        "installed_software": installed_software,
    }


def _enforce_agent_scope(
    agent_token,
    *,
    connector_type: str | None = None,
    asset_tags: list[str] | None = None,
    cr_type: str | None = None,
    required_role: str = "read",
) -> None:
    """Enforce AgentToken scope constraints. No-op if agent_token is None (ApiToken path)."""
    if agent_token is None:
        return
    from fastapi import HTTPException
    if required_role not in (agent_token.allowed_roles or []):
        raise HTTPException(403, f"Agent token does not have '{required_role}' permission")
    if connector_type and agent_token.allowed_connector_types:
        if connector_type not in agent_token.allowed_connector_types:
            raise HTTPException(403, f"Agent token not authorized for connector type '{connector_type}'")
    if asset_tags and agent_token.allowed_asset_tags:
        if not any(t in agent_token.allowed_asset_tags for t in asset_tags):
            raise HTTPException(403, "Agent token not authorized for any of the asset's tags")
    if cr_type and agent_token.allowed_cr_types:
        if cr_type not in agent_token.allowed_cr_types:
            raise HTTPException(403, f"Agent token not authorized for CR type '{cr_type}'")
