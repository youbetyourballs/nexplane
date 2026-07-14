# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Reference scan orchestrator — fans out to AWS, K8s, and nexplane_agent connectors,
resolves consumer identities, triages hits via AI, and persists exceptions."""

import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.scan_exception import ScanException
from app.services.identity_resolution import resolve_consumer_identity
from app.services.reference_triage import triage_scan_hits

logger = logging.getLogger(__name__)


async def _run_aws_scans(cr, connector, db) -> list:
    """Run all 6 AWS reference scan actions and aggregate hits."""
    from app.connectors.executors.aws.reference_scan import (
        scan_lambda_env_vars,
        scan_ecs_task_defs,
        scan_rds_parameter_groups,
        scan_secrets_manager_metadata,
        scan_ssm_parameters_metadata,
        scan_ec2_user_data,
    )
    hits = []
    for fn in [
        scan_lambda_env_vars,
        scan_ecs_task_defs,
        scan_rds_parameter_groups,
        scan_secrets_manager_metadata,
        scan_ssm_parameters_metadata,
        scan_ec2_user_data,
    ]:
        try:
            result = await fn(cr, connector, db)
            hits.extend(result.get("hits", []))
        except Exception as exc:
            logger.warning("AWS scan %s failed: %s", fn.__name__, exc)
    return hits


async def _run_k8s_scans(cr, connector, db) -> list:
    """Run all 4 Kubernetes reference scan actions and aggregate hits."""
    from app.connectors.executors.kubernetes.reference_scan import (
        scan_configmaps,
        scan_secrets_metadata,
        scan_deployment_env,
        scan_ingress_rules,
    )
    hits = []
    for fn in [
        scan_configmaps,
        scan_secrets_metadata,
        scan_deployment_env,
        scan_ingress_rules,
    ]:
        try:
            result = await fn(cr, connector, db)
            hits.extend(result.get("hits", []))
        except Exception as exc:
            logger.warning("K8s scan %s failed: %s", fn.__name__, exc)
    return hits


async def _run_agent_scans(cr, connector, asset_ids, db) -> list:
    """Run nexplane_agent host reference scan for each asset_id."""
    from app.connectors.executors.nexplane_agent.scan_host_references import execute
    hits = []
    for asset_id in asset_ids:
        # Build a lightweight CR proxy with the asset scoped in
        proxy_cr = type("_ScanCR", (), {
            "parameters": cr.parameters,
            "asset_id": asset_id,
        })()
        try:
            result = await execute(proxy_cr, connector, db)
            hits.extend(result.get("hits", []))
        except Exception as exc:
            logger.warning("Agent scan for asset %s failed: %s", asset_id, exc)
    return hits


async def orchestrate_scan(cr, db: AsyncSession, secrets_svc, settings) -> dict:
    """Fan out reference scans across all configured connectors, resolve identities,
    triage with AI, persist exceptions, and return a summary dict."""
    params = cr.parameters or {}
    org_id = cr.organization_id
    migration_context = params.get("migration_context", {})
    connector_configs = params.get("connectors", [])

    all_hits = []

    for cc in connector_configs:
        connector_type = cc.get("connector_type")
        connector_id = cc.get("connector_id")

        from app.models.connector import Connector
        r = await db.execute(select(Connector).where(Connector.id == connector_id))
        connector = r.scalar_one_or_none()
        if not connector:
            logger.warning("Connector %s not found, skipping", connector_id)
            continue

        if connector_type == "aws":
            hits = await _run_aws_scans(cr, connector, db)
        elif connector_type == "kubernetes":
            hits = await _run_k8s_scans(cr, connector, db)
        elif connector_type == "nexplane_agent":
            hits = await _run_agent_scans(cr, connector, cc.get("asset_ids", []), db)
        else:
            logger.warning("Unknown connector type %s, skipping", connector_type)
            continue

        all_hits.extend(hits)

    # Identity resolution — tier 4 hits get auto-registered as new assets
    resolved = []
    assets_registered = 0
    for hit in all_hits:
        res = await resolve_consumer_identity(db, org_id, hit.get("consumer_identity", {}))
        asset_id = res.asset_id
        if res.tier == 4 and res.new_asset_data:
            new_asset = Asset(
                id=uuid.uuid4(),
                organization_id=org_id,
                name=res.new_asset_data["name"],
                asset_type=res.new_asset_data["asset_type"],
                environment=res.new_asset_data.get("environment", "unknown"),
                criticality="low",
                asset_metadata=res.new_asset_data.get("asset_metadata", {}),
            )
            db.add(new_asset)
            await db.flush()
            asset_id = new_asset.id
            assets_registered += 1
        resolved.append({
            "hit": hit,
            "asset_id": asset_id,
            "tier": res.tier,
            "confidence": res.confidence,
        })

    # AI triage
    triage = await triage_scan_hits(resolved, migration_context, settings, secrets_svc)

    # Persist exceptions to DB
    for exc in triage.exceptions:
        hit_index = exc.get("hit_index")
        hit_data = resolved[hit_index]["hit"] if hit_index is not None and hit_index < len(resolved) else {}
        exc_asset_id = exc.get("asset_id") or (resolved[hit_index]["asset_id"] if hit_index is not None and hit_index < len(resolved) else None)
        db.add(ScanException(
            organization_id=org_id,
            scan_cr_id=cr.id,
            consumer_asset_id=exc_asset_id,
            matched_term=hit_data.get("matched_term", ""),
            location=hit_data.get("location", ""),
            surface=hit_data.get("surface", ""),
            snippet=hit_data.get("snippet", ""),
            reason=exc.get("reason", ""),
            suggested_action=exc.get("suggested_action", ""),
            confidence=exc.get("confidence", 0.0),
            status="pending",
        ))
    await db.commit()

    return {
        "hits_total": len(all_hits),
        "confident_updates": triage.confident_updates,
        "exceptions_created": len(triage.exceptions),
        "assets_registered": assets_registered,
    }
