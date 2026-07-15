# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    deployment_name = parameters["deployment_name"]
    now = datetime.now(timezone.utc).isoformat()

    if not creds:
        return {
            "action": "restart_deployment",
            "namespace": namespace,
            "deployment_name": deployment_name,
            "restarted_at": now,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        apps = clients["apps"]
        dep = apps.read_namespaced_deployment(deployment_name, namespace)
        annotations = dep.spec.template.metadata.annotations or {}
        previous_restart = annotations.get("kubectl.kubernetes.io/restartedAt")

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                    }
                }
            }
        }
        apps.patch_namespaced_deployment(deployment_name, namespace, patch)
        return {
            "previous_restart": previous_restart,
            "resource_version": dep.metadata.resource_version,
        }

    snapshot = await loop.run_in_executor(None, _call)
    return {
        "action": "restart_deployment",
        "namespace": namespace,
        "deployment_name": deployment_name,
        "restarted_at": now,
        "rollback_data": snapshot,
        "executed_at": now,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Pods were restarted from the same image. No meaningful rollback — re-deploy if needed.",
        "snapshot": execution_result.get("rollback_data"),
    }
