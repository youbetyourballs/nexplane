# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_workloads", "deployments": [{"name": "mock-app", "namespace": "default", "replicas": 3}], "count": 1}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    dep_list = await loop.run_in_executor(None, lambda: clients["apps"].list_deployment_for_all_namespaces())
    deployments = [{"name": d.metadata.name, "namespace": d.metadata.namespace, "replicas": d.spec.replicas, "ready": d.status.ready_replicas} for d in dep_list.items]
    return {"action": "discover_workloads", "deployments": deployments, "count": len(deployments)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
