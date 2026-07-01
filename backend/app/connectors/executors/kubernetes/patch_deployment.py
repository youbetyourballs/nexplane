# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    deployment_name = parameters["deployment_name"]
    namespace = parameters["namespace"]
    patch = parameters["patch"]
    if not creds:
        return {"action": "patch_deployment", "deployment_name": deployment_name, "namespace": namespace, "patched": True}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    await loop.run_in_executor(None, lambda: clients["apps"].patch_namespaced_deployment(deployment_name, namespace, patch))
    return {"action": "patch_deployment", "deployment_name": deployment_name, "namespace": namespace, "patched": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore previous deployment spec manually or via git"}
