import asyncio
import json
import yaml
from datetime import datetime, timezone


def _parse_manifest(manifest_str: str) -> dict:
    try:
        return json.loads(manifest_str)
    except json.JSONDecodeError:
        return yaml.safe_load(manifest_str)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    manifest_str = parameters["manifest"]
    namespace = parameters.get("namespace")
    manifest = _parse_manifest(manifest_str)
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {"action": "update_rbac", "kind": kind, "name": name, "applied": True, "mock": True}

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        rbac = clients["rbac"]
        is_cluster_scoped = kind == "ClusterRoleBinding"

        try:
            if is_cluster_scoped:
                existing = rbac.read_cluster_role_binding(name)
            else:
                existing = rbac.read_namespaced_role_binding(name, namespace)
            rollback_data = {"existed": True, "previous": existing.to_dict()}
        except Exception:
            rollback_data = {"existed": False, "name": name, "namespace": namespace, "kind": kind}

        try:
            if is_cluster_scoped:
                rbac.patch_cluster_role_binding(name, manifest)
            else:
                rbac.patch_namespaced_role_binding(name, namespace, manifest)
        except Exception:
            if is_cluster_scoped:
                rbac.create_cluster_role_binding(manifest)
            else:
                rbac.create_namespaced_role_binding(namespace, manifest)

        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "update_rbac",
        "kind": kind,
        "name": name,
        "namespace": namespace,
        "applied": True,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Restore previous RBAC binding by re-applying rollback_data manifest manually.",
        "rollback_data": execution_result.get("rollback_data"),
    }
