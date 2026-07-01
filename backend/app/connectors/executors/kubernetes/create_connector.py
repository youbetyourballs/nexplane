# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor for register_kubernetes_connector action.

Registers a Kubernetes cluster as a connector by storing its kubeconfig in
the ConnectorCredential table.  If a kubernetes connector already references
the same asset (via credentials.cluster_asset_id) it is returned as-is.
"""
from __future__ import annotations
import base64
import uuid


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Register a Kubernetes cluster as a connector.

    Parameters:
      - kubeconfig_b64 (str, required): base64-encoded kubeconfig YAML
      - endpoint (str, required): Kubernetes API server URL
      - cluster_name (str, optional): human-readable cluster name
      - region (str, optional): cloud region where the cluster lives
    """
    kubeconfig_b64: str = parameters.get("kubeconfig_b64", "")
    endpoint: str = parameters.get("endpoint", "")
    cluster_name: str = parameters.get("cluster_name", "")
    region: str = parameters.get("region", "")

    if not kubeconfig_b64:
        raise ValueError("Missing required parameter: kubeconfig_b64")
    if not endpoint:
        raise ValueError("Missing required parameter: endpoint")

    # Validate that kubeconfig_b64 is valid base64.
    try:
        kubeconfig_bytes = base64.b64decode(kubeconfig_b64, validate=True)
        kubeconfig_str = kubeconfig_bytes.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"kubeconfig_b64 is not valid base64: {exc}") from exc

    # Verify the cluster is reachable before writing anything to the DB.
    await _verify_cluster_reachable(kubeconfig_str)

    asset_id = asset_ids[0] if asset_ids else None

    from app.database import AsyncSessionLocal
    from app.models.connector import Connector, ConnectorType, ConnectorStatus
    from app.models.connector_credential import ConnectorCredential
    from app.services.secrets_service import SecretsService
    from app import config as app_config
    from sqlalchemy import select

    organization_id = getattr(connector, "organization_id", None)
    if organization_id is None:
        raise ValueError("Connector object does not have an organization_id; cannot register cluster.")

    svc = SecretsService(app_config.settings.SECRET_KEY)

    async with AsyncSessionLocal() as db:
        # Check whether a kubernetes connector already references this asset.
        existing_connector_id: uuid.UUID | None = None
        if asset_id:
            result = await db.execute(
                select(Connector).where(
                    Connector.organization_id == organization_id,
                    Connector.connector_type == ConnectorType.kubernetes,
                )
            )
            k8s_connectors = result.scalars().all()
            for conn in k8s_connectors:
                cred_result = await db.execute(
                    select(ConnectorCredential).where(ConnectorCredential.connector_id == conn.id)
                )
                cred_row = cred_result.scalar_one_or_none()
                if cred_row:
                    try:
                        creds = svc.decrypt_json(cred_row.credentials_encrypted)
                    except Exception:
                        creds = {}
                    if str(creds.get("cluster_asset_id", "")) == str(asset_id):
                        return {
                            "connector_id": str(conn.id),
                            "status": "already_registered",
                            "endpoint": endpoint,
                        }

        # Create a new Connector record.
        display_name = cluster_name or f"kubernetes-cluster-{str(asset_id or uuid.uuid4())[:8]}"
        new_connector = Connector(
            organization_id=organization_id,
            connector_type=ConnectorType.kubernetes,
            name=display_name,
            status=ConnectorStatus.active,
            scoped_permissions={},
        )
        db.add(new_connector)
        await db.flush()

        # Store credentials (kubeconfig + metadata) encrypted.
        credentials_payload = {
            "kubeconfig": kubeconfig_str,
            "kubeconfig_b64": kubeconfig_b64,
            "endpoint": endpoint,
            "cluster_asset_id": str(asset_id) if asset_id else None,
            "region": region,
        }
        encrypted = svc.encrypt_json(credentials_payload)

        cred = ConnectorCredential(
            connector_id=new_connector.id,
            organization_id=organization_id,
            credentials_encrypted=encrypted,
            updated_by=None,
        )
        db.add(cred)
        await db.commit()
        connector_id = str(new_connector.id)

    auto_asset = {
        "name": display_name,
        "asset_type": "kubernetes_cluster",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "endpoint": endpoint,
            "cluster_name": cluster_name,
            "region": region,
            "connector_id": connector_id,
        },
        "tags": ["kubernetes", "cluster"],
    }

    return {
        "connector_id": connector_id,
        "status": "registered",
        "endpoint": endpoint,
        "_auto_asset": auto_asset,
    }


async def _verify_cluster_reachable(kubeconfig_str: str) -> None:
    """Make a lightweight k8s API call to confirm the cluster is reachable.

    Raises RuntimeError if the cluster cannot be contacted so that callers
    fail before writing any DB records.
    """
    import asyncio
    import yaml
    from kubernetes import client as k8s_client
    from kubernetes.config.kube_config import KubeConfigLoader

    def _check() -> None:
        config_dict = yaml.safe_load(kubeconfig_str)
        loader = KubeConfigLoader(config_dict=config_dict)
        configuration = k8s_client.Configuration()
        loader.load_and_set(configuration)
        api_client = k8s_client.ApiClient(configuration=configuration)
        try:
            k8s_client.CoreV1Api(api_client=api_client).list_namespace(limit=1, timeout_seconds=10)
        finally:
            api_client.rest_client.pool_manager.clear()

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _check)
    except Exception as exc:
        raise RuntimeError(f"Kubernetes cluster unreachable: {exc}") from exc


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: deactivate the newly registered connector."""
    connector_id = execution_result.get("connector_id")
    status = execution_result.get("status")
    if not connector_id or status == "already_registered":
        return {"rolled_back": False, "note": "Nothing to roll back."}

    from app.database import AsyncSessionLocal
    from app.models.connector import Connector, ConnectorStatus
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
        conn = result.scalar_one_or_none()
        if conn:
            conn.status = ConnectorStatus.inactive
            db.add(conn)
            await db.commit()
            return {"rolled_back": True, "connector_id": connector_id, "note": "Connector set to inactive."}

    return {"rolled_back": False, "note": f"Connector {connector_id} not found."}
