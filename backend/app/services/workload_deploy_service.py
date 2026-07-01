# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Service to register a Kubernetes workload asset after k8s_workload_deploy."""
import uuid
from datetime import datetime, timezone
from app.models.asset import Asset, AssetType, Environment, Criticality


async def register_workload_asset(db, asset_ids: list, execution_result: dict) -> None:
    """Create a kubernetes_workload asset in inventory after successful deploy."""
    deploy_data = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and result.get("action") == "k8s_workload_deploy":
            deploy_data = result
            break
    if deploy_data is None or deploy_data.get("dry_run"):
        return

    app_name = deploy_data.get("app_name", "unknown")
    namespace = deploy_data.get("namespace", "default")
    cluster_asset_id_str = deploy_data.get("cluster_asset_id", "")

    source_org_id = None
    if asset_ids:
        source = await db.get(Asset, uuid.UUID(str(asset_ids[0])))
        if source:
            source_org_id = source.organization_id

    if not source_org_id:
        return

    workload = Asset(
        organization_id=source_org_id,
        name=f"{app_name} (k8s/{namespace})",
        asset_type=AssetType.kubernetes_workload,
        environment=Environment.prod,
        criticality=Criticality.medium,
        tags=["containerized", "kubernetes"],
        asset_metadata={
            "app_name": app_name,
            "namespace": namespace,
            "cluster_asset_id": cluster_asset_id_str,
            "pod_count": deploy_data.get("pod_count", 1),
            "service_ip": deploy_data.get("service_ip", ""),
            "source_asset_ids": [str(a) for a in asset_ids],
            "deployed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(workload)
    await db.commit()
