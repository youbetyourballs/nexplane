from __future__ import annotations
from datetime import datetime, timezone
import asyncio
from ._client import get_k8s_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Rotate a Kubernetes ServiceAccount token by deleting the old secret."""
    sa_name = parameters.get("service_account", "")
    namespace = parameters.get("namespace", "default")
    if not sa_name:
        raise ValueError("service_account is required")

    clients = get_k8s_client(connector, parameters)
    if not clients:
        return {"action": "k8s_rotate_sa_token", "status": "skipped",
                "reason": "no_k8s_credentials"}

    loop = asyncio.get_event_loop()

    def _rotate():
        core = clients["core"]
        # Find token secrets for this SA
        secrets = core.list_namespaced_secret(namespace=namespace)
        deleted = []
        for secret in secrets.items:
            if (secret.type == "kubernetes.io/service-account-token" and
                    secret.metadata.annotations and
                    secret.metadata.annotations.get("kubernetes.io/service-account.name") == sa_name):
                core.delete_namespaced_secret(name=secret.metadata.name, namespace=namespace)
                deleted.append(secret.metadata.name)
        return {"deleted_secrets": deleted}

    result = await loop.run_in_executor(None, _rotate)
    return {
        "action": "k8s_rotate_sa_token",
        "service_account": sa_name,
        "namespace": namespace,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        **result,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Token rotation is one-way — new token created automatically by k8s"}
