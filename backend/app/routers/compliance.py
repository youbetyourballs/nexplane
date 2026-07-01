# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.compliance.cis_v8_map import compute_cis_summary
from app.models.asset import Asset, AssetType
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.compliance import ComplianceBaseline, ChangeFreezeWindow
from app.routers import current_user
from app.models.user import User
from pydantic import BaseModel as _PydanticBase
from app.models.compliance_attestation import ComplianceAttestation
from app.schemas.compliance import (
    ComplianceBaselineCreate,
    ComplianceBaselineRead,
    ComplianceBaselineUpdate,
    ChangeFreezeWindowCreate,
    ChangeFreezeWindowRead,
    CisSummaryResponse,
    EvidenceCollectionRequest,
)


class AttestRequest(_PydanticBase):
    evidence_description: str
    expiry_days: int | None = 365

router = APIRouter(prefix="/compliance", tags=["Compliance"])


# ---- Baselines CRUD ----

@router.get("/baselines", response_model=list[ComplianceBaselineRead])
async def list_baselines(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ComplianceBaseline)
        .where(ComplianceBaseline.organization_id == user.organization_id)
        .order_by(ComplianceBaseline.created_at.desc())
    )
    return result.scalars().all()


@router.post("/baselines", response_model=ComplianceBaselineRead, status_code=201)
async def create_baseline(
    body: ComplianceBaselineCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = ComplianceBaseline(
        organization_id=user.organization_id,
        **body.model_dump(),
    )
    db.add(baseline)
    await db.commit()
    await db.refresh(baseline)
    return baseline


@router.get("/baselines/{baseline_id}", response_model=ComplianceBaselineRead)
async def get_baseline(
    baseline_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    return baseline


@router.put("/baselines/{baseline_id}", response_model=ComplianceBaselineRead)
async def update_baseline(
    baseline_id: uuid.UUID,
    body: ComplianceBaselineUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    if body.config is not None and body.config != baseline.config:
        # Save current config snapshot to history before overwriting
        history = list(baseline.history or [])
        history.append(baseline.config)
        baseline.history = history
        baseline.version += 1
        baseline.config = body.config
    if body.name is not None:
        baseline.name = body.name
    if body.description is not None:
        baseline.description = body.description
    if body.auto_execute is not None:
        baseline.auto_execute = body.auto_execute
    baseline.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(baseline)
    return baseline


@router.delete("/baselines/{baseline_id}", status_code=204)
async def delete_baseline(
    baseline_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    await db.delete(baseline)
    await db.commit()


async def _get_baseline(
    db: AsyncSession, baseline_id: uuid.UUID, org_id: uuid.UUID
) -> ComplianceBaseline:
    result = await db.execute(
        select(ComplianceBaseline).where(
            ComplianceBaseline.id == baseline_id,
            ComplianceBaseline.organization_id == org_id,
        )
    )
    baseline = result.scalar_one_or_none()
    if not baseline:
        raise HTTPException(status_code=404, detail="Compliance baseline not found")
    return baseline


# ---- Drift alerts ----

@router.get("/drift-alerts")
async def list_drift_alerts(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return assets that have open DRAFT enforce_cis_benchmark change requests (drift-detected)."""
    result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.organization_id == user.organization_id,
            ChangeRequest.change_type == "enforce_cis_benchmark",
            ChangeRequest.status == ChangeRequestStatus.draft,
        ).order_by(ChangeRequest.created_at.desc())
    )
    crs = result.scalars().all()
    return [
        {
            "change_request_id": str(cr.id),
            "title": cr.title,
            "target_asset_ids": cr.target_asset_ids,
            "created_at": cr.created_at.isoformat(),
        }
        for cr in crs
    ]


# ---- Evidence collection ----

@router.post("/evidence-collection")
async def collect_evidence(
    body: EvidenceCollectionRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dispatch collect_evidence agent job to each asset, aggregate results,
    and stream a ZIP archive containing all artifacts.
    """
    # Resolve assets
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.id.in_(body.asset_ids),
        )
    )
    assets = result.scalars().all()
    if not assets:
        raise HTTPException(status_code=404, detail="No matching assets found")

    # Dispatch agent jobs and collect results
    from app.services.agent_job_service import dispatch_agent_job
    job_results = []
    for asset in assets:
        try:
            job_result = await dispatch_agent_job(
                db=db,
                asset_id=asset.id,
                command="collect_evidence",
                parameters={
                    "framework": body.framework,
                    "control_id": body.control_id,
                    "evidence_types": body.evidence_types,
                },
            )
            job_results.append((asset, job_result))
        except Exception as exc:
            job_results.append((asset, {"error": str(exc), "artifacts": []}))

    # Fetch change logs from DB for each asset
    change_logs: dict[str, list] = {}
    if "change_logs" in body.evidence_types:
        from sqlalchemy import and_
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        for asset in assets:
            cr_result = await db.execute(
                select(ChangeRequest).where(
                    and_(
                        ChangeRequest.organization_id == user.organization_id,
                        ChangeRequest.created_at >= cutoff,
                    )
                )
            )
            crs = cr_result.scalars().all()
            asset_id_str = str(asset.id)
            change_logs[asset_id_str] = [
                {
                    "id": str(cr.id),
                    "title": cr.title,
                    "change_type": cr.change_type.value if hasattr(cr.change_type, "value") else cr.change_type,
                    "status": cr.status.value if hasattr(cr.status, "value") else cr.status,
                    "created_at": cr.created_at.isoformat(),
                }
                for cr in crs
                if asset_id_str in (cr.target_asset_ids or [])
            ]

    # Build ZIP in memory
    buf = io.BytesIO()
    collected_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "framework": body.framework,
        "control_id": body.control_id,
        "collected_at": collected_at,
        "nexplane_version": "0.1.0",
        "assets": [a.name or str(a.id) for a in assets],
        "evidence_types": body.evidence_types,
    }

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for asset, job_result in job_results:
            hostname = getattr(asset, "name", None) or str(asset.id)
            artifacts = job_result.get("artifacts", [])
            for artifact in artifacts:
                if artifact.get("type") == "config_file":
                    filename = artifact["path"].lstrip("/").replace("/", "_")
                    zf.writestr(f"{hostname}/{filename}", artifact.get("content", ""))
                elif artifact.get("type") == "command_output":
                    cmd_safe = artifact["command"].replace(" ", "_").replace("/", "_")
                    zf.writestr(f"{hostname}/{cmd_safe}.txt", artifact.get("output", ""))
            if "change_logs" in body.evidence_types:
                asset_id_str = str(asset.id)
                logs = change_logs.get(asset_id_str, [])
                zf.writestr(f"{hostname}/change_log.json", json.dumps(logs, indent=2))

    buf.seek(0)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"evidence-{body.framework}-{body.control_id}-{date_str}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- Attestation ----

@router.post("/controls/{control_id}/attest", status_code=201)
async def attest_control(
    control_id: str,
    body: AttestRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record a manual attestation for a compliance control."""
    from datetime import timedelta
    expiry = None
    if body.expiry_days:
        expiry = datetime.now(timezone.utc) + timedelta(days=body.expiry_days)
    att = ComplianceAttestation(
        organization_id=user.organization_id,
        control_id=control_id,
        attested_by=user.id,
        evidence_description=body.evidence_description,
        expires_at=expiry,
    )
    db.add(att)
    await db.commit()
    return {
        "id": str(att.id),
        "control_id": control_id,
        "expires_at": att.expires_at.isoformat() if att.expires_at else None,
    }


@router.get("/controls/{control_id}/attestations")
async def list_attestations(
    control_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all attestations for a compliance control."""
    result = await db.execute(
        select(ComplianceAttestation).where(
            ComplianceAttestation.organization_id == user.organization_id,
            ComplianceAttestation.control_id == control_id,
        ).order_by(ComplianceAttestation.created_at.desc())
    )
    atts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "control_id": a.control_id,
            "attested_by": str(a.attested_by),
            "evidence_description": a.evidence_description,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "created_at": a.created_at.isoformat(),
        }
        for a in atts
    ]


@router.post("/controls/{control_id}/remediate-all", status_code=201)
async def remediate_all_for_control(
    control_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a batch of CRs to remediate all failing assets for a compliance control."""
    import uuid as _uuid
    from app.models.change_request import ChangeRequest as _CR, ChangeType, ChangeRequestStatus

    # Get failing assets from CIS summary
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.asset_type == AssetType.server,
        )
    )
    assets = result.scalars().all()
    asset_dicts = [
        {
            "id": str(a.id),
            "name": a.name,
            "connector_id": str(a.connector_id) if a.connector_id else None,
            "asset_metadata": a.asset_metadata or {},
        }
        for a in assets
    ]
    summary = compute_cis_summary(asset_dicts)
    failing_asset_ids: list[str] = []
    for ctrl in summary.get("controls", []):
        if str(ctrl.get("id")) == control_id:
            for chk in ctrl.get("checks", []):
                failing_asset_ids.extend(a["id"] for a in chk.get("failing_assets", []))
            break

    failing_asset_ids = list(dict.fromkeys(failing_asset_ids))  # deduplicate, preserve order

    if not failing_asset_ids:
        return {"batch_id": None, "cr_ids": [], "message": "No failing assets found"}

    batch_id = _uuid.uuid4()
    cr_ids = []
    for asset_id in failing_asset_ids[:50]:
        cr = _CR(
            id=_uuid.uuid4(),
            organization_id=user.organization_id,
            requester_id=user.id,
            title=f"CIS Control {control_id} remediation on {asset_id[:8]}",
            change_type=ChangeType.enforce_cis_benchmark,
            target_asset_ids=[asset_id],
            desired_outcome={"control_id": control_id, "profile": "cis_level1"},
            batch_id=batch_id,
            status=ChangeRequestStatus.draft,
            source="compliance_fix_all",
        )
        db.add(cr)
        cr_ids.append(str(cr.id))
    await db.commit()
    return {"batch_id": str(batch_id), "cr_ids": cr_ids}


# ---- Freeze windows ----

@router.get("/freeze-windows/active", response_model=Optional[ChangeFreezeWindowRead])
async def get_active_freeze(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current active freeze window or 404."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChangeFreezeWindow)
        .where(ChangeFreezeWindow.start_at <= now, ChangeFreezeWindow.end_at >= now)
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/freeze-windows", response_model=list[ChangeFreezeWindowRead])
async def list_freeze_windows(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeFreezeWindow).order_by(ChangeFreezeWindow.start_at.desc())
    )
    return result.scalars().all()


@router.post("/freeze-windows", response_model=ChangeFreezeWindowRead, status_code=201)
async def create_freeze_window(
    body: ChangeFreezeWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    freeze = ChangeFreezeWindow(
        reason=body.reason,
        start_at=body.start_at,
        end_at=body.end_at,
        emergency_bypass_role=body.emergency_bypass_role,
        created_by=user.id,
    )
    db.add(freeze)
    await db.commit()
    await db.refresh(freeze)
    return freeze


@router.delete("/freeze-windows/{freeze_id}", status_code=204)
async def delete_freeze_window(
    freeze_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeFreezeWindow).where(ChangeFreezeWindow.id == freeze_id)
    )
    freeze = result.scalar_one_or_none()
    if not freeze:
        raise HTTPException(status_code=404, detail="Freeze window not found")
    await db.delete(freeze)
    await db.commit()


# ---- CIS Controls v8 summary ----

@router.get("/cis-summary", response_model=CisSummaryResponse)
async def get_cis_summary(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the 18-control CIS Controls v8 compliance summary for the org."""
    from sqlalchemy import func as sqlfunc
    from app.models.policy_baseline import PolicyBaseline
    from app.models.vulnerability import VulnerabilityFinding
    from app.models.connector import Connector, ConnectorType

    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.asset_type == AssetType.server,
        )
    )
    assets = result.scalars().all()

    asset_dicts = [
        {
            "id": str(a.id),
            "name": a.name,
            "connector_id": str(a.connector_id) if a.connector_id else None,
            "asset_metadata": a.asset_metadata or {},
        }
        for a in assets
    ]

    # Control 4: PolicyBaseline coverage
    total_servers = len(assets)
    hardened_count = await db.scalar(
        select(sqlfunc.count(sqlfunc.distinct(PolicyBaseline.asset_id))).where(
            PolicyBaseline.organization_id == user.organization_id
        )
    ) or 0
    policy_baseline_counts = {"total_servers": total_servers, "hardened_count": hardened_count}

    # Control 7: vulnerability findings within SLA
    total_vulns = await db.scalar(
        select(sqlfunc.count(VulnerabilityFinding.id)).where(
            VulnerabilityFinding.organization_id == user.organization_id,
        )
    ) or 0
    within_sla = await db.scalar(
        select(sqlfunc.count(VulnerabilityFinding.id)).where(
            VulnerabilityFinding.organization_id == user.organization_id,
            VulnerabilityFinding.status.in_(["remediated", "mitigated"]),
        )
    ) or 0
    vuln_sla_data = {"total": total_vulns, "within_sla": within_sla}

    # Control 10: assets linked via EDR connectors (CrowdStrike, Defender, SentinelOne)
    edr_types = [ConnectorType.crowdstrike, ConnectorType.defender_endpoint, ConnectorType.sentinelone]
    edr_connectors_result = await db.execute(
        select(Connector.id).where(
            Connector.organization_id == user.organization_id,
            Connector.connector_type.in_(edr_types),
        )
    )
    edr_connector_ids = {str(row[0]) for row in edr_connectors_result.fetchall()}
    edr_asset_ids: set[str] = {
        a["id"] for a in asset_dicts
        if a.get("connector_id") and a["connector_id"] in edr_connector_ids
    }

    summary = compute_cis_summary(
        asset_dicts,
        policy_baseline_counts=policy_baseline_counts,
        vuln_sla_data=vuln_sla_data,
        edr_asset_ids=edr_asset_ids if edr_connector_ids else None,
    )
    return CisSummaryResponse(**summary)
