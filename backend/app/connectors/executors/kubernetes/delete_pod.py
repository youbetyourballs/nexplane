# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    pod_name = parameters["pod_name"]
    namespace = parameters["namespace"]
    if not creds:
        return {"action": "delete_pod", "pod_name": pod_name, "namespace": namespace, "deleted": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    await loop.run_in_executor(None, lambda: clients["core"].delete_namespaced_pod(pod_name, namespace))
    return {"action": "delete_pod", "pod_name": pod_name, "namespace": namespace, "deleted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "pod will be rescheduled automatically by its controller"}
