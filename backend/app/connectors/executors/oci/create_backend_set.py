# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")
    name = parameters.get("name", "nexplane-backend-set")
    policy = parameters.get("policy", "ROUND_ROBIN")
    health_checker = parameters.get("health_checker", {
        "protocol": "HTTP",
        "port": 80,
        "url_path": "/health",
        "return_code": 200,
        "interval_ms": 10000,
        "timeout_in_millis": 3000,
        "retries": 3,
    })

    if not creds:
        return {
            "action": "create_backend_set",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "name": name,
            "policy": policy,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    hc = oci_sdk.load_balancer.models.HealthCheckerDetails(
        protocol=health_checker.get("protocol", "HTTP"),
        port=int(health_checker.get("port", 80)),
        url_path=health_checker.get("url_path", "/health"),
        return_code=int(health_checker.get("return_code", 200)),
        interval_in_millis=int(health_checker.get("interval_ms", 10000)),
        timeout_in_millis=int(health_checker.get("timeout_in_millis", 3000)),
        retries=int(health_checker.get("retries", 3)),
    )
    details = oci_sdk.load_balancer.models.CreateBackendSetDetails(
        name=name,
        policy=policy,
        health_checker=hc,
        backends=[],
    )

    work_req = await loop.run_in_executor(
        None, lambda: lb_client.create_backend_set(load_balancer_id, details)
    )

    return {
        "action": "create_backend_set",
        "load_balancer_id": load_balancer_id,
        "name": name,
        "policy": policy,
        "work_request_id": work_req.headers.get("opc-work-request-id"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the backend set."""
    creds = getattr(connector, "credentials", {})
    load_balancer_id = execution_result.get("load_balancer_id", parameters.get("load_balancer_id"))
    name = execution_result.get("name", parameters.get("name", "nexplane-backend-set"))

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    await loop.run_in_executor(
        None, lambda: lb_client.delete_backend_set(load_balancer_id, name)
    )
    return {"rolled_back": True, "load_balancer_id": load_balancer_id, "backend_set_name": name}
