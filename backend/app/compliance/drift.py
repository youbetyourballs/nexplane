# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceBaseline
from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel

logger = logging.getLogger(__name__)


def detect_drift(audit_result: dict, baseline: ComplianceBaseline) -> list[str]:
    """Return list of failing control IDs not marked 'skip' in baseline overrides."""
    overrides = baseline.config.get("overrides", {})
    return [
        c["id"]
        for c in audit_result.get("controls", [])
        if c["status"] == "fail" and overrides.get(c["id"]) != "skip"
    ]


def _build_updated_metadata(existing_metadata: dict, audit_result: dict) -> dict:
    """Pure function — builds updated metadata dict with cis_compliance key updated."""
    metadata = dict(existing_metadata or {})
    prev = metadata.get("cis_compliance", {})
    history = list(prev.get("history", []))

    # Push the old latest into history
    if "latest" in prev:
        history.append({
            "score": prev["latest"]["score"],
            "level": prev["latest"]["level"],
            "collected_at": prev["latest"]["collected_at"],
        })

    # Keep last 30 snapshots
    history = history[-30:]

    metadata["cis_compliance"] = {
        "latest": audit_result,
        "history": history,
    }
    return metadata


async def write_cis_score_to_metadata(
    db: AsyncSession, asset_id: uuid.UUID, audit_result: dict
) -> None:
    """Persist audit_result into asset.metadata under the cis_compliance key."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        logger.warning("write_cis_score_to_metadata: asset %s not found", asset_id)
        return
    asset.metadata = _build_updated_metadata(asset.metadata or {}, audit_result)
    await db.flush()


async def _resolve_scope(
    db: AsyncSession, scope_type: str, scope_value: str, org_id: uuid.UUID
) -> list[Asset]:
    """Return assets matching the baseline scope (asset UUID or tag string)."""
    if scope_type == "asset":
        try:
            asset_id = uuid.UUID(scope_value)
        except ValueError:
            return []
        result = await db.execute(
            select(Asset).where(Asset.id == asset_id, Asset.organization_id == org_id)
        )
        asset = result.scalar_one_or_none()
        return [asset] if asset else []
    elif scope_type == "tag":
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == org_id,
                Asset.tags.contains([scope_value]),
            )
        )
        return list(result.scalars().all())
    return []


async def _create_drift_change_request(
    db: AsyncSession,
    asset: Asset,
    baseline: ComplianceBaseline,
    drifted_controls: list[str],
    audit_result: dict,
) -> ChangeRequest:
    """Create a DRAFT enforce_cis_benchmark change request for drifted controls."""
    drift_params = {
        "level": baseline.cis_level,
        "os_family": baseline.os_family,
        "asset_ids": [str(asset.id)],
        "dry_run": False,
        "baseline_id": str(baseline.id),
        "drifted_controls": drifted_controls,
        "before_score": audit_result.get("score"),
    }
    cr = ChangeRequest(
        organization_id=baseline.organization_id,
        # system-generated: use a placeholder UUID (cannot be None due to FK constraint)
        requester_id=baseline.organization_id,
        title=f"[Drift] {asset.name or asset.id}: {len(drifted_controls)} controls drifted from '{baseline.name}'",
        description=(
            f"Drift detection found {len(drifted_controls)} controls failing baseline '{baseline.name}' "
            f"(CIS Level {baseline.cis_level}, {baseline.os_family}). "
            f"Drifted control IDs: {', '.join(drifted_controls[:10])}{'...' if len(drifted_controls) > 10 else ''}."
        ),
        change_type=ChangeType.enforce_cis_benchmark,
        target_asset_ids=[str(asset.id)],
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
        desired_outcome=drift_params,
    )
    db.add(cr)
    await db.flush()
    return cr


async def run_drift_detection(db: AsyncSession) -> None:
    """
    Weekly job: for each ComplianceBaseline, fetch matching assets, dispatch
    audit_cis_compliance, compare to baseline, and create DRAFT change requests
    for drifted hosts. If baseline.auto_execute is True, also approves and executes.
    """
    result = await db.execute(select(ComplianceBaseline))
    baselines = result.scalars().all()

    for baseline in baselines:
        try:
            assets = await _resolve_scope(db, baseline.scope_type, baseline.scope_value, baseline.organization_id)
        except Exception as exc:
            logger.error("Drift detection: failed to resolve scope for baseline %s: %s", baseline.id, exc)
            continue

        for asset in assets:
            try:
                from app.services.agent_job_service import dispatch_agent_job
                job_result = await dispatch_agent_job(
                    db=db,
                    asset_id=asset.id,
                    command="audit_cis_compliance",
                    parameters={"level": baseline.cis_level, "os_family": baseline.os_family},
                )
                await write_cis_score_to_metadata(db, asset.id, job_result)

                drifted = detect_drift(job_result, baseline)
                if not drifted:
                    logger.info("Drift detection: asset %s is compliant with baseline %s", asset.id, baseline.id)
                    continue

                logger.info(
                    "Drift detected: asset %s has %d drifted controls from baseline %s",
                    asset.id, len(drifted), baseline.id,
                )
                cr = await _create_drift_change_request(db, asset, baseline, drifted, job_result)

                if baseline.auto_execute:
                    from app.workflows import runner as workflow_runner
                    from app.models.execution_run import ExecutionRun, ExecutionStatus
                    cr.status = ChangeRequestStatus.approved
                    run = ExecutionRun(
                        change_request_id=cr.id,
                        workflow_id=f"wf-drift-{cr.id}",
                        status=ExecutionStatus.pending,
                    )
                    db.add(run)
                    await db.flush()
                    await workflow_runner.run_workflow(db, run.id)

            except Exception as exc:
                logger.error("Drift detection failed for asset %s: %s", asset.id, exc)

    await db.commit()
