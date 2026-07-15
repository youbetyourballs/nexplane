# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_k8s_clients


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    namespace = parameters.get("namespace", "default")
    deployment_name = parameters["deployment_name"]
    patch_body = parameters["patch"]

    if not creds:
        return {
            "patched": True,
            "deployment_name": deployment_name,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    api = clients["apps"]

    # 1. Capture full spec before patching
    def _get():
        dep = api.read_namespaced_deployment(deployment_name, namespace)
        return dep.to_dict() if hasattr(dep, "to_dict") else {}

    prior_spec = await loop.run_in_executor(None, _get)
    pre_state = {"spec": prior_spec}

    # 2. Apply patch
    def _patch():
        api.patch_namespaced_deployment(deployment_name, namespace, patch_body)

    await loop.run_in_executor(None, _patch)
    return {
        "patched": True,
        "deployment_name": deployment_name,
        "namespace": namespace,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})

    if not pre_state or "spec" not in pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    deployment_name = execution_result.get("deployment_name") or parameters.get("deployment_name")
    namespace = execution_result.get("namespace") or parameters.get("namespace", "default")

    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    api = clients["apps"]

    try:
        prior = pre_state["spec"]

        def _restore():
            restore_body = {"spec": prior.get("spec", {})}
            api.patch_namespaced_deployment(deployment_name, namespace, restore_body)

        await loop.run_in_executor(None, _restore)
        return {
            "rolled_back": True,
            "deployment_name": deployment_name,
            "namespace": namespace,
        }
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
