# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
    namespace = parameters["namespace"]
    manifest_str = parameters["manifest"]
    manifest = _parse_manifest(manifest_str)
    policy_name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {
            "action": "apply_network_policy",
            "namespace": namespace,
            "policy_name": policy_name,
            "applied": True,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        networking = clients["networking"]
        try:
            existing = networking.read_namespaced_network_policy(policy_name, namespace)
            existing_dict = existing.to_dict()
            rollback_data = {"existed": True, "previous": existing_dict}
        except Exception:
            rollback_data = {"existed": False, "name": policy_name, "namespace": namespace}

        try:
            networking.patch_namespaced_network_policy(policy_name, namespace, manifest)
        except Exception:
            networking.create_namespaced_network_policy(namespace, manifest)
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "apply_network_policy",
        "namespace": namespace,
        "policy_name": policy_name,
        "applied": True,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rollback_data = execution_result.get("rollback_data", {})
    namespace = parameters["namespace"]
    manifest = _parse_manifest(parameters["manifest"])
    policy_name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {"action": "restore_network_policy", "namespace": namespace, "mock": True}

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _restore():
        networking = clients["networking"]
        if rollback_data.get("existed"):
            networking.replace_namespaced_network_policy(policy_name, namespace, rollback_data["previous"])
        else:
            networking.delete_namespaced_network_policy(policy_name, namespace)

    await loop.run_in_executor(None, _restore)
    return {"action": "restore_network_policy", "namespace": namespace, "policy_name": policy_name, "rolled_back": True}
