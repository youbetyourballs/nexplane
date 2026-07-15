# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_k8s_client

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Remove a RoleBinding or ClusterRoleBinding to revoke access."""
    import asyncio
    name = parameters.get("rolebinding_name", "")
    namespace = parameters.get("namespace", "default")
    binding_type = parameters.get("binding_type", "RoleBinding")  # or ClusterRoleBinding

    if not name:
        raise ValueError("rolebinding_name is required")

    clients = get_k8s_client(connector, parameters)
    if not clients:
        return {"action": "k8s_revoke_rolebinding", "status": "skipped",
                "reason": "no_k8s_credentials", "name": name}

    loop = asyncio.get_event_loop()

    def _delete():
        rbac = clients["rbac"]
        if binding_type == "ClusterRoleBinding":
            rbac.delete_cluster_role_binding(name=name)
        else:
            rbac.delete_namespaced_role_binding(name=name, namespace=namespace)
        return {"deleted": True}

    result = await loop.run_in_executor(None, _delete)
    return {
        "action": "k8s_revoke_rolebinding",
        "name": name, "namespace": namespace,
        "binding_type": binding_type,
        "revoked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "RoleBinding deletion cannot be auto-reversed — recreate manually"}
