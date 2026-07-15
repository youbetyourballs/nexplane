# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    deployment_name = parameters["deployment_name"]
    replicas = parameters["replicas"]

    if not creds:
        return {
            "action": "scale_deployment",
            "namespace": namespace,
            "deployment_name": deployment_name,
            "replicas": replicas,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        apps = clients["apps"]
        current_scale = apps.read_namespaced_deployment_scale(deployment_name, namespace)
        previous_replicas = current_scale.spec.replicas or 0
        apps.patch_namespaced_deployment_scale(
            deployment_name, namespace, {"spec": {"replicas": replicas}}
        )
        return previous_replicas

    previous_replicas = await loop.run_in_executor(None, _call)
    return {
        "action": "scale_deployment",
        "namespace": namespace,
        "deployment_name": deployment_name,
        "replicas": replicas,
        "previous_replicas": previous_replicas,
        "rollback_data": {"previous_replicas": previous_replicas},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    previous = execution_result.get("previous_replicas", execution_result.get("rollback_data", {}).get("previous_replicas"))
    rollback_params = {**parameters, "replicas": previous}
    return await execute(rollback_params, [], connector)
