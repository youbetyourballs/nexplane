# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

from ._client import get_container_engine_client

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")

    if not creds:
        return {
            "action": "get_oke_kubeconfig",
            "cluster_id": cluster_id,
            "kubeconfig": "apiVersion: v1\nkind: Config\n# mock\n",
            "mock": True,
        }

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    response = await loop.run_in_executor(None, lambda: client.create_kubeconfig(cluster_id))
    raw = response.data.content
    kubeconfig_yaml = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    return {
        "action": "get_oke_kubeconfig",
        "cluster_id": cluster_id,
        "kubeconfig": kubeconfig_yaml,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "rollback_get_oke_kubeconfig",
        "rolled_back": False,
        "reason": "read-only",
    }
