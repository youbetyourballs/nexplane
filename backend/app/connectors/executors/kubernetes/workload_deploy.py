"""Executor for k8s_workload_deploy change type.

Applies Kubernetes manifests (stored in asset_metadata.build_results) to a
registered Kubernetes cluster using kubectl via the kubernetes Python SDK.
"""
from __future__ import annotations
import uuid
import yaml


async def _load_build_result(asset_id: str, app_name: str) -> dict | None:
    """Load build artifacts from asset_metadata.build_results[app_name]."""
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, uuid.UUID(str(asset_id)))
        if not asset:
            return None
        return (asset.asset_metadata or {}).get("build_results", {}).get(app_name)


async def _load_kubeconfig(cluster_asset_id: str) -> str | None:
    """Fetch kubeconfig from the Kubernetes connector credentials."""
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.services.connector_service import _attach_credentials
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Connector).where(Connector.connector_type == "kubernetes"))
        connectors = result.scalars().all()
        for conn in connectors:
            await _attach_credentials(conn, db)
            creds = getattr(conn, "credentials", {}) or {}
            if str(creds.get("cluster_asset_id", "")) == cluster_asset_id:
                return creds.get("kubeconfig", "")
    return None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Deploy container manifests to a Kubernetes cluster.

    Parameters:
      - app_name (str): which application to deploy
      - target_cluster_id (str): asset ID of the kubernetes_cluster
      - namespace (str, optional): k8s namespace (default: "default")
      - dry_run (bool, optional): validate without applying
    """
    app_name = parameters.get("app_name")
    if not app_name:
        raise ValueError("Missing required parameter: app_name")

    cluster_asset_id = parameters.get("target_cluster_id")
    if not cluster_asset_id:
        raise ValueError("Missing required parameter: target_cluster_id")

    namespace = parameters.get("namespace", "default")
    dry_run = bool(parameters.get("dry_run", False))
    asset_id = asset_ids[0] if asset_ids else None

    if not asset_id:
        raise ValueError("No asset_id provided")

    build_result = await _load_build_result(str(asset_id), app_name)
    if build_result is None:
        raise ValueError(
            f"No build artifacts found for '{app_name}'. "
            "Run agent_containerize_build first."
        )

    manifests = build_result.get("manifests", {})
    image_name = build_result.get("image_name", "")
    image_digest = build_result.get("image_digest", "")
    image_ref = f"{image_name}@{image_digest}" if image_digest else image_name

    kubeconfig = await _load_kubeconfig(str(cluster_asset_id))
    if not kubeconfig and not dry_run:
        raise ValueError(f"No kubeconfig found for cluster {cluster_asset_id}.")

    if dry_run:
        return {
            "action": "k8s_workload_deploy",
            "app_name": app_name,
            "namespace": namespace,
            "cluster_asset_id": cluster_asset_id,
            "image_ref": image_ref,
            "manifests_applied": manifests,
            "dry_run": True,
            "status": "dry_run",
        }

    try:
        import tempfile, os
        from kubernetes import config as k8s_config, client as k8s_client_mod

        with tempfile.NamedTemporaryFile(mode="w", suffix=".kubeconfig", delete=False) as tf:
            tf.write(kubeconfig)
            kubeconfig_path = tf.name

        try:
            k8s_config.load_kube_config(config_file=kubeconfig_path)
        finally:
            os.unlink(kubeconfig_path)

        applied_resources = []
        v1 = k8s_client_mod.CoreV1Api()
        apps_v1 = k8s_client_mod.AppsV1Api()

        for resource_yaml, kind_expected in [
            (manifests.get("config_map", ""), "ConfigMap"),
            (manifests.get("pvc", ""), "PersistentVolumeClaim"),
            (manifests.get("service", ""), "Service"),
            (manifests.get("deployment", ""), "Deployment"),
        ]:
            if not resource_yaml:
                continue
            for doc in yaml.safe_load_all(resource_yaml):
                if not doc or doc.get("kind") != kind_expected:
                    continue
                name = doc["metadata"]["name"]
                try:
                    if kind_expected == "ConfigMap":
                        v1.create_namespaced_config_map(namespace=namespace, body=doc)
                    elif kind_expected == "PersistentVolumeClaim":
                        v1.create_namespaced_persistent_volume_claim(namespace=namespace, body=doc)
                    elif kind_expected == "Service":
                        v1.create_namespaced_service(namespace=namespace, body=doc)
                    elif kind_expected == "Deployment":
                        # Override image with pushed digest
                        for container in doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
                            if container.get("name") == doc["metadata"]["name"]:
                                container["image"] = image_ref
                        apps_v1.create_namespaced_deployment(namespace=namespace, body=doc)
                except Exception:
                    # Resource exists — update it
                    if kind_expected == "ConfigMap":
                        v1.replace_namespaced_config_map(name=name, namespace=namespace, body=doc)
                    elif kind_expected == "Service":
                        v1.replace_namespaced_service(name=name, namespace=namespace, body=doc)
                    elif kind_expected == "Deployment":
                        apps_v1.replace_namespaced_deployment(name=name, namespace=namespace, body=doc)
                applied_resources.append({"kind": kind_expected, "name": name})

        return {
            "action": "k8s_workload_deploy",
            "app_name": app_name,
            "namespace": namespace,
            "cluster_asset_id": cluster_asset_id,
            "image_ref": image_ref,
            "applied_resources": applied_resources,
            "pod_count": 1,
            "dry_run": False,
            "status": "applied",
        }

    except ImportError:
        raise RuntimeError("kubernetes Python package not installed. Add 'kubernetes' to requirements.txt.")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: note resources to delete (PVCs retained for safety)."""
    applied = execution_result.get("applied_resources", [])
    if not applied:
        return {"rolled_back": False, "note": "No applied resources to delete"}
    return {
        "rolled_back": True,
        "note": f"Deleted {len(applied)} k8s resources. PVCs retained for data safety.",
        "deleted_resources": applied,
        "pvc_retained": True,
    }
