import asyncio
import base64
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    secret_name = parameters["secret_name"]
    data = parameters["data"]  # plain-text key-value pairs
    restart_deployments = parameters.get("restart_deployments", [])

    if not creds:
        return {
            "action": "rotate_secret",
            "namespace": namespace,
            "secret_name": secret_name,
            "keys_rotated": list(data.keys()),
            "deployments_restarted": restart_deployments,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    now = datetime.now(timezone.utc).isoformat()

    def _call():
        core = clients["core"]
        apps = clients["apps"]

        existing = core.read_namespaced_secret(secret_name, namespace)
        previous_keys = list((existing.data or {}).keys())

        encoded_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
        core.patch_namespaced_secret(secret_name, namespace, {"data": encoded_data})

        deployments_restarted = []
        targets = list(restart_deployments)
        if not targets:
            all_deps = apps.list_namespaced_deployment(namespace)
            for dep in all_deps.items:
                volumes = dep.spec.template.spec.volumes or []
                for vol in volumes:
                    if vol.secret and vol.secret.secret_name == secret_name:
                        targets.append(dep.metadata.name)
                        break
                else:
                    for container in dep.spec.template.spec.containers:
                        for env_from in (container.env_from or []):
                            if env_from.secret_ref and env_from.secret_ref.name == secret_name:
                                targets.append(dep.metadata.name)
                                break

        for dep_name in targets:
            try:
                apps.patch_namespaced_deployment(
                    dep_name, namespace,
                    {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
                )
                deployments_restarted.append(dep_name)
            except Exception:
                pass

        return {"previous_keys": previous_keys, "deployments_restarted": deployments_restarted}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "rotate_secret",
        "namespace": namespace,
        "secret_name": secret_name,
        "keys_rotated": list(data.keys()),
        "deployments_restarted": result["deployments_restarted"],
        "rollback_data": {"previous_keys": result["previous_keys"]},
        "executed_at": now,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Secret rollback requires re-applying previous values. Store rollback values in SecretsService before executing.",
        "previous_keys": execution_result.get("rollback_data", {}).get("previous_keys", []),
    }
