# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")
    name = parameters.get("name", "nexplane-listener")
    default_backend_set = parameters.get("default_backend_set", "nexplane-backend-set")
    port = int(parameters.get("port", 80))
    protocol = parameters.get("protocol", "HTTP")

    if not creds:
        return {
            "action": "create_listener",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "name": name,
            "port": port,
            "protocol": protocol,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    details = oci_sdk.load_balancer.models.CreateListenerDetails(
        name=name,
        default_backend_set_name=default_backend_set,
        port=port,
        protocol=protocol,
    )

    work_req = await loop.run_in_executor(
        None, lambda: lb_client.create_listener(load_balancer_id, details)
    )

    return {
        "action": "create_listener",
        "load_balancer_id": load_balancer_id,
        "name": name,
        "default_backend_set": default_backend_set,
        "port": port,
        "protocol": protocol,
        "work_request_id": work_req.headers.get("opc-work-request-id"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the listener."""
    creds = getattr(connector, "credentials", {})
    load_balancer_id = execution_result.get("load_balancer_id", parameters.get("load_balancer_id"))
    name = execution_result.get("name", parameters.get("name", "nexplane-listener"))

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    await loop.run_in_executor(
        None, lambda: lb_client.delete_listener(load_balancer_id, name)
    )
    return {"rolled_back": True, "load_balancer_id": load_balancer_id, "listener_name": name}
