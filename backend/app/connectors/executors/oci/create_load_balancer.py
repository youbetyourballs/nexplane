import asyncio
import time
from datetime import datetime, timezone


_POLL_INTERVAL_SECONDS = 15
_MAX_WAIT_SECONDS = 900  # 15 minutes


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-lb")
    shape_name = parameters.get("shape_name", "flexible")
    shape_min_mbps = int(parameters.get("shape_min_mbps", 10))
    shape_max_mbps = int(parameters.get("shape_max_mbps", 100))
    subnet_ids = parameters.get("subnet_ids", [])
    is_private = parameters.get("is_private", False)

    auto_asset = {
        "name": display_name,
        "asset_type": "load_balancer",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "load_balancer_id": "ocid1.loadbalancer.mock",
            "shape_name": shape_name,
            "shape_min_mbps": shape_min_mbps,
            "shape_max_mbps": shape_max_mbps,
            "ip_addresses": [],
            "lifecycle_state": "ACTIVE",
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "is_private": is_private,
            "provider": "oci",
        },
        "tags": ["oci", "load-balancer", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_load_balancer",
            "load_balancer_id": "ocid1.loadbalancer.mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client, get_compartment_id
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    shape_details = oci_sdk.load_balancer.models.ShapeDetails(
        minimum_bandwidth_in_mbps=shape_min_mbps,
        maximum_bandwidth_in_mbps=shape_max_mbps,
    )
    details = oci_sdk.load_balancer.models.CreateLoadBalancerDetails(
        compartment_id=comp_id,
        display_name=display_name,
        shape_name=shape_name,
        shape_details=shape_details,
        subnet_ids=subnet_ids,
        is_private=is_private,
    )

    # Submit creation — returns a work request
    work_request_resp = await loop.run_in_executor(
        None, lambda: lb_client.create_load_balancer(details)
    )
    work_request_id = work_request_resp.headers.get("opc-work-request-id")

    # Poll work request until SUCCEEDED (up to 15 min)
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    lb_id = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        wr = await loop.run_in_executor(
            None, lambda: lb_client.get_work_request(work_request_id).data
        )
        if wr.lifecycle_state == "SUCCEEDED":
            if not lb_id and wr.load_balancer_id:
                lb_id = wr.load_balancer_id
            break
        if wr.lifecycle_state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"OCI load balancer creation work request {wr.lifecycle_state}: {wr.message}")

    if not lb_id:
        raise RuntimeError("Timed out waiting for OCI load balancer to become ACTIVE")

    # Fetch final LB details
    lb = await loop.run_in_executor(None, lambda: lb_client.get_load_balancer(lb_id).data)
    ip_addrs = [ip.ip_address for ip in (lb.ip_addresses or [])]

    auto_asset["asset_metadata"].update({
        "load_balancer_id": lb_id,
        "ip_addresses": ip_addrs,
        "lifecycle_state": lb.lifecycle_state,
        "compartment_id": lb.compartment_id,
    })

    return {
        "action": "create_load_balancer",
        "load_balancer_id": lb_id,
        "display_name": lb.display_name,
        "shape_name": lb.shape_name,
        "ip_addresses": ip_addrs,
        "is_private": lb.is_private,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_load_balancer import execute as delete
    return await delete(
        {"load_balancer_id": execution_result.get("load_balancer_id")},
        [],
        connector,
    )
